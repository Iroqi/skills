#!/usr/bin/env python3
"""Content-to-Video — 渲染命令的"不等进程退出"包装器

**要解决的问题**：`npx hyperframes render -o out.mp4 ...` 背后是 headless
Chrome/Puppeteer。已经观察到的现象是：
MP4 文件已经完整写出（ffmpeg mux 完成、文件大小不再变化），但 Node 进程本身
没有退出（很可能是 Puppeteer 的 browser.close() 没被正确 await，或者有个
残留的 setInterval/文件监听把事件循环挂住了）。如果 agent 直接在前台跑这条
命令等它退出，遇到这种情况会一直卡住拿不到结果，导致对话看起来"没反应"，
而实际上视频早就渲染好了。

**这个脚本的做法**：不等进程退出，而是把渲染命令丢到后台，轮询目标输出文件
——文件出现且连续 N 次轮询大小不变，就认为渲染已经完成；随后先等进程自然
退出（最多 12s 宽限期，覆盖 MP4 收尾 moov 重写的写入停顿，期间继续监视），
宽限期过才**强制收掉整棵进程树**（Windows 用
`taskkill /T /F`，POSIX 用进程组信号），避免残留的 Node/Chrome 进程一直占着
不退出——这正是"视频早渲染好了、但对话窗口一直拿不到结果"的根因。

**子进程日志**：渲染命令的 stderr 会重定向到 `<输出文件>.render.log`（如
`out/video.mp4` → `out/video.render.log`），失败或超时时打印日志路径和
末尾若干行，方便定位 Hyperframes/Chrome 的具体报错。stdout 仍丢弃（渲染进度
刷屏没有保留价值）。

Usage（替代直接跑 `npx hyperframes render -o out.mp4`）：
  python scripts/render_watch.py -o out/video.mp4 -- \
    npx hyperframes render -o out/video.mp4 --quality standard

参数：
  -o/--output       要等待出现的目标文件（渲染命令自己的 -o 参数指向的路径）
  --poll-interval   轮询间隔秒数，默认 2
  --stable-checks   文件大小连续多少次轮询不变才算"写完"，默认 2
                     （即至少 poll-interval * stable-checks 秒没有新数据写入）
  --max-wait        最长等待秒数，默认 1800（30 分钟），超时仍会报错退出
  --                之后的所有参数是实际要执行的渲染命令
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import setup_stdio  # noqa: E402  重定向场景 stderr 强制 UTF-8


# 输出文件判定"稳定"后，给渲染进程自然退出的宽限期上限（秒）。MP4 收尾
# 要重写 moov box，期间文件大小可能短暂不变——稳定即强杀会截断收尾产坏片。
# 12s 的依据：实测 110s 成片的 assemble（moov/混流）阶段仅 ~3s，12s 已留
# 4 倍余量；进程真挂住时（pitfalls #16 的常见场景）每轮渲染省下 18s 空等。
# 截断风险由下游 verify_render 兜底（时长/编码校验不过会拦下）。
_EXIT_GRACE_SECONDS = 12.0


def _print_log_tail(log_path, max_lines=25):
    """打印渲染日志文件末尾若干行（用于失败/超时时的排查线索）。"""
    if not log_path or not os.path.isfile(log_path):
        return
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    tail = lines[-max_lines:]
    print(f"[render_watch] 渲染日志末尾（完整日志: {log_path}）:",
          file=sys.stderr)
    for line in tail:
        print("    " + line.rstrip(), file=sys.stderr)


def _resolve_npx_shim(shim_path):
    """把 npx.cmd shim 解析成 [node.exe, npx-cli.js] 直调形态。

    npx.cmd 本质是 `node "<shim目录>/node_modules/npm/bin/npx-cli.js" %*`
    的包装。直接 spawn 这一对可以完全绕开 cmd.exe 中转：
    - 少一层进程，调用更快；
    - 没有 cmd.exe 的引号二次解析问题（/c 后整串含 & 或引号时的
      拦腰截断/转义地狱，见历史：/d /s /c + 外层引号方案会被 Popen 的
      list2cmdline 二次转义成 \\" 开头，cmd 不剥引号、整串被当命令名）。

    返回列表或 None（shim 结构不符预期时，由调用方走 comspec 回退）。
    """
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    node_exe = os.path.join(shim_dir, "node.exe")
    npx_cli = os.path.join(shim_dir, "node_modules", "npm", "bin", "npx-cli.js")
    if os.path.isfile(node_exe) and os.path.isfile(npx_cli):
        return [node_exe, npx_cli]
    return None


def resolve_command(cmd):
    """把命令列表里的可执行名解析成可被 Python 直接 spawn 的形式。

    背景：Windows 上 `npx` 只有 npx / npx.cmd（没有 npx.exe），
    subprocess.Popen(["npx", ...]) 的 CreateProcess 无法直接启动 .cmd 文件，
    会以 FileNotFoundError / WinError 193 失败。这里统一解析：
    - POSIX：shutil.which 后直接用绝对路径；
    - Windows 且命中 npx.cmd：解析 shim 直调 node + npx-cli.js（见
      _resolve_npx_shim；解析失败才回退 %COMSPEC% /c 中转）；
    - Windows 其他 .cmd/.bat：交给 %COMSPEC% /c 启动（注意：该路径下
      参数含 `&` 等 cmd 元字符时会被当命令分隔符——npx 直调路径不存在
      此问题，.bat 极少用于带特殊字符参数的场景）。
    """
    if not cmd:
        return cmd
    exe = shutil.which(cmd[0])
    if not exe:
        return cmd  # 让 Popen 给出正常的"命令未找到"错误
    if os.name == "nt" and not exe.lower().endswith((".exe", ".cmd", ".bat")):
        # Windows 上 `where npx` 可能先命中无扩展名的 sh 包装脚本
        # （如 C:\Program Files\nodejs\npx），CreateProcess 无法启动它。
        # 优先改找带扩展名的可执行文件。
        for ext in (".exe", ".cmd", ".bat"):
            alt = shutil.which(cmd[0] + ext)
            if alt:
                exe = alt
                break
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        if exe.lower().endswith(".cmd"):
            direct = _resolve_npx_shim(exe)
            if direct is not None:
                return direct + cmd[1:]
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c"] + cmd
    return [exe] + cmd[1:]


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="包装渲染命令：不等进程退出，轮询输出文件是否已写完")
    parser.add_argument("-o", "--output", required=True,
                         help="渲染命令会产出的目标文件路径")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--stable-checks", type=int, default=2)
    parser.add_argument("--grace", type=float, default=_EXIT_GRACE_SECONDS,
                        help="文件稳定后等待进程自然退出的宽限期秒数"
                             "（默认 12；覆盖 moov 收尾写入停顿，超时才强杀）")
    parser.add_argument("--max-wait", type=float, default=1800.0)
    parser.add_argument("cmd", nargs=argparse.REMAINDER,
                         help="'--' 之后的实际渲染命令")
    args = parser.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[error] 未提供要执行的渲染命令（'--' 之后应跟实际命令）",
              file=sys.stderr)
        sys.exit(2)

    out_path = args.output
    log_path = os.path.splitext(os.path.abspath(out_path))[0] + ".render.log"
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # 渲染开始前如果目标文件已存在（比如重跑一次覆盖旧产物），先删掉，
    # 否则轮询一开始就会看到"文件已存在且大小不变"，误判成已经渲染完成。
    if os.path.exists(out_path):
        # 旧成片正被播放器占用（Windows 常见）时裸 os.remove 直接炸栈，
        # 渲染还没开始就崩——给出可行动的提示而不是 traceback
        try:
            os.remove(out_path)
        except OSError as e:
            print(f"[error] 无法删除旧的输出文件 {out_path}（被播放器/资源"
                  f"管理器占用？请关闭占用它的程序后重试）: {e}", file=sys.stderr)
            sys.exit(1)
    # 同样清理上一次的日志，避免新旧日志混在一起误导排查。
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass

    print(f"[render_watch] 启动: {' '.join(cmd)}", file=sys.stderr)
    print(f"[render_watch] 子进程 stderr 日志: {log_path}", file=sys.stderr)
    # start_new_session：POSIX 下让子进程单独成组、可按组发信号（Windows
    # 上该参数被 CPython 忽略，是 no-op——Windows 的进程树收尾由 _try_kill
    # 的 taskkill /T /F 按 PID 树兜底）。
    # 先置 None 再进 try：若 open 本身失败（权限/磁盘问题），finally 里的
    # close 检查才不会 NameError
    log_fh = None
    resolved_cmd = resolve_command(cmd)
    print(f"[render_watch] 解析后的命令: {' '.join(resolved_cmd)}", file=sys.stderr)
    try:
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(
            resolved_cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
            start_new_session=True,
        )

        t_start = time.time()
        last_size = -1
        last_mtime = -1.0
        stable_count = 0
        grace_deadline = None
        while True:
            elapsed = time.time() - t_start
            if elapsed > args.max_wait:
                print(f"[render_watch] 超过 max-wait ({args.max_wait}s) 仍未见到"
                      f"稳定的输出文件，放弃等待。", file=sys.stderr)
                _try_kill(proc)
                _print_log_tail(log_path)
                sys.exit(1)

            # 进程自己正常退出了（没有卡住），按退出码走正常判断逻辑。
            ret = proc.poll()
            if ret is not None:
                if ret != 0:
                    print(f"[render_watch] 渲染命令退出码 {ret}（非正常退出）",
                          file=sys.stderr)
                    _print_log_tail(log_path)
                    sys.exit(ret)
                if os.path.exists(out_path):
                    print(f"[render_watch] 进程正常退出，输出文件存在: {out_path}",
                          file=sys.stderr)
                    return
                print("[render_watch] 进程退出但目标文件不存在，判定渲染失败",
                      file=sys.stderr)
                _print_log_tail(log_path)
                sys.exit(1)

            # 进程还没退出——检查目标文件是否已经出现且大小稳定不变。
            # 大小 + mtime 双条件：只看大小时，编码器写入停顿超过稳定窗口
            #（系统休眠唤醒、磁盘拥塞）会被误判"渲染完成"并强杀进程，
            # 产出截断的 MP4。mtime 停变说明写入端真的收手了。
            if os.path.exists(out_path):
                size = os.path.getsize(out_path)
                try:
                    mtime = os.path.getmtime(out_path)
                except OSError:
                    mtime = -1.0
                if size > 0 and size == last_size and mtime == last_mtime:
                    stable_count += 1
                else:
                    stable_count = 0
                    # 文件又开始变化说明上一轮"稳定"是误报（收尾写入暂停后
                    # 恢复），宽限期作废、重新计稳
                    grace_deadline = None
                last_size = size
                last_mtime = mtime
                if stable_count >= args.stable_checks:
                    # MP4 收尾要停写一小段重写 moov box——"文件大小不变"不
                    # 代表 mux 已收尾。判定稳定后先给进程最多 grace 秒自然
                    # 退出的宽限期（期间继续轮询，进程自然退出走上方 poll
                    # 分支），宽限期过才强杀，避免把 moov 重写拦腰截断产坏片。
                    if grace_deadline is None:
                        grace_deadline = time.time() + args.grace
                        print(f"[render_watch] 输出文件 {out_path} 大小已连续 "
                              f"{args.stable_checks} 次轮询不变（{size} bytes），"
                              f"判定渲染已完成；等待进程自然退出（最多 "
                              f"{args.grace:.0f}s 宽限期，超时才强制"
                              f"结束，用时 {elapsed:.1f}s）……", file=sys.stderr)
                    elif time.time() >= grace_deadline:
                        print(f"[render_watch] 宽限期已过进程仍未退出，强制结束"
                              f"进程树（用时 {elapsed:.1f}s）。", file=sys.stderr)
                        _try_kill(proc)
                        return
            else:
                # 输出文件不存在（尚未出现，或被渲染器中途删除/改名）：
                # 重置稳定计数与上次采样值，重新出现时从头计稳，
                # 避免残留的 size/mtime 让下一轮误判"已经稳定"
                stable_count = 0
                last_size = -1
                last_mtime = -1.0

            time.sleep(args.poll_interval)
    finally:
        # open 失败时 log_fh 仍为 None，不能直接 close
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass


def _try_kill(proc):
    """收掉残留进程树，失败也不影响已经产出的文件。

    Windows 上没有 os.killpg，POSIX 的 SIGTERM/SIGKILL 在 Windows 是空操作，
    会导致 Node/Chrome 进程残留；这里用 taskkill /T /F 按进程树强制结束。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


if __name__ == "__main__":
    main()
