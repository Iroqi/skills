/* Content-to-Video — 浏览器预览（仅人工预览用，渲染/check 完全 inert）。
 *
 * 由 gen_hyperframes.py 生成 composition 时自动复制到 HTML 输出目录，
 * index.html 只通过外部脚本引用 preview.js（注意：注释中不要出现字面
 * 量“/script”尖括号序列，否则 hyperframes 单文件打包内联时会截断）。
 * 打开 index.html：真实浏览器直接进入预览（默认停在首帧、点击播放、窗口自适应、
 * 底部控制条、进度条、播完重播）；Hyperframes 渲染/check 走 headless（navigator.webdriver
 * === true 且 URL 无 ?preview）时第一行退出，不影响出片。
 */
(function () {
  // 显式 ?preview 参数（精确匹配键名）强制进入预览——headless 调试时用
  var wantPreview = new URLSearchParams(location.search).has('preview');
  // headless 判定用两个独立信号：navigator.webdriver 覆盖标准自动化栈；
  // UA 的 HeadlessChrome 覆盖不置 webdriver 标志的 CDP 直连环境（旧/新
  // headless 构建都带）。两者任一命中且无显式参数都不注入预览 UI，
  // 防止渲染帧里混进控制条。
  var ua = navigator.userAgent || '';
  var isHeadless = navigator.webdriver === true || ua.indexOf('HeadlessChrome') >= 0;
  if (!wantPreview && isHeadless) return;

  var root = document.getElementById('root');
  if (!root) return;
  var W = parseInt(root.dataset.width, 10) || 1920;
  var H = parseInt(root.dataset.height, 10) || 1080;
  var dur = parseFloat(root.dataset.duration || '0');
  var tl = (window.__timelines && window.__timelines['main']) || null;
  var srcEl = document.getElementById('main-audio');
  var audio = (srcEl && srcEl.getAttribute('src'))
      ? new Audio(srcEl.getAttribute('src')) : null;

  // webview 里 composition 的 width=1920 meta 会触发自动缩放，覆盖为 device-width
  var vp = document.querySelector('meta[name="viewport"]');
  if (vp) vp.setAttribute('content', 'width=device-width, initial-scale=1');

  var CTRL_H = 52;
  var playing = false;
  var dragging = false;

  // 布局：上方画面 + 底部控制条（控制条在画面下方，绝不遮挡）
  var holder = document.createElement('div');
  holder.style.cssText = 'position:fixed;inset:0;display:flex;flex-direction:column;background:#0d0f16';
  var stage = document.createElement('div');
  stage.style.cssText = 'flex:1;min-height:0;position:relative;overflow:hidden';
  var ctrl = document.createElement('div');
  ctrl.innerHTML =
      '<button id="pv-play" type="button" style="border:0;background:#fff;color:#111;' +
      'border-radius:999px;padding:7px 18px;cursor:pointer;font-weight:600;font-size:13px">' +
      '▶ 播放</button>' +
      '<input id="pv-seek" type="range" min="0" max="100" value="0" ' +
      'style="width:min(320px,40vw);accent-color:#4fc3f7;cursor:pointer">' +
      '<span id="pv-time" style="min-width:118px;text-align:center;' +
      'font-variant-numeric:tabular-nums">0.0 / 0.0s</span>';
  ctrl.style.cssText = 'flex:none;height:' + CTRL_H + 'px;display:flex;' +
      'align-items:center;justify-content:center;gap:12px;background:#0d0f16;' +
      'border-top:1px solid rgba(255,255,255,0.12);color:#eef2f7;' +
      'font:13px/1.4 system-ui,sans-serif';
  document.body.appendChild(holder);
  holder.appendChild(stage);
  stage.appendChild(root);
  holder.appendChild(ctrl);

  // root 内部全是绝对定位子元素，必须显式给尺寸，否则 transform 后不可见
  root.style.width = W + 'px';
  root.style.height = H + 'px';
  root.style.position = 'absolute';
  root.style.left = '0px';
  root.style.top = '0px';
  root.style.margin = '0';

  document.documentElement.style.width = '100%';
  document.documentElement.style.height = '100%';
  document.body.style.width = '100%';
  document.body.style.height = '100%';
  document.body.style.margin = '0';
  document.body.style.overflow = 'hidden';

  var btn = ctrl.querySelector('#pv-play');
  var seek = ctrl.querySelector('#pv-seek');
  var timeEl = ctrl.querySelector('#pv-time');
  // dur 有效时进度条用秒刻度；缺失（data-duration 没写）时保持 0-100
  // 百分比刻度，拖动值按 tl.duration() 折算成秒——直接把百分比当秒喂给
  // GSAP 会被钳制不崩但语义完全错位
  var secScale = dur > 0;
  if (secScale) seek.max = dur;
  function _tlDuration() { return (tl && tl.duration()) ? tl.duration() : 0; }
  function _seekToSeconds(raw) {
    return secScale ? raw : (raw / 100) * _tlDuration();
  }

  function fit() {
    var sw = window.innerWidth;
    var sh = window.innerHeight - CTRL_H;
    var s = Math.min(1, sw / W, sh / H);
    root.style.transformOrigin = '0 0';
    root.style.transform = 'scale(' + s + ')';
    root.style.left = Math.max(0, (sw - W * s) / 2) + 'px';
    root.style.top = Math.max(0, (sh - H * s) / 2) + 'px';
  }
  function update() {
    var t = tl ? tl.time() : 0;
    if (!dragging) seek.value = secScale ? t : (_tlDuration() ? (t / _tlDuration()) * 100 : 0);
    timeEl.textContent = t.toFixed(1) + ' / ' + (dur || 0).toFixed(1) + 's';
  }
  function syncAudio() {
    if (!audio || !tl) return;
    var t = tl.time();
    // 音频未缓冲（readyState<2）时 currentTime 不可靠：恒为 0 会让差值
    // 恒 >0.15、每帧强制 seek，音频永远无法正常播放
    if (audio.readyState >= 2 && Math.abs(audio.currentTime - t) > 0.15) {
      audio.currentTime = t;
    }
    if (dur > 0 && t >= dur - 0.05 && playing) {
      playing = false;
      btn.textContent = '▶ 播放';
      audio.pause();
    }
  }
  function toggle() {
    if (!tl) return;
    if (playing) {
      tl.pause(); if (audio) audio.pause();
      playing = false; btn.textContent = '▶ 播放';
    } else {
      // 播完后再点播放：回到 0s 重新开始（与常见播放器一致）
      if (dur > 0 && tl.time() >= dur - 0.05) {
        tl.time(0);
        if (audio) audio.currentTime = 0;
      }
      tl.play();
      if (audio) { audio.currentTime = tl.time(); audio.play().catch(function () {}); }
      playing = true; btn.textContent = '⏸ 暂停';
    }
  }
  // 直接挂到 composition 自带的字幕 onUpdate（window.__pvUpdate 钩子）
  window.__pvUpdate = function () { update(); syncAudio(); };

  seek.addEventListener('input', function () {
    dragging = true;
    var v = _seekToSeconds(parseFloat(seek.value));
    if (tl) tl.time(v);
    if (audio) audio.currentTime = v;
    update();
  });
  seek.addEventListener('change', function () { dragging = false; });
  btn.addEventListener('click', toggle);
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space') { e.preventDefault(); toggle(); }
  });
  window.addEventListener('resize', fit);

  fit(); update();

  // 默认不自动播放：停在首帧，等用户点击“播放”（用户手势下音频不会被浏览器拦截）。
  // 播放/暂停由 btn 点击或空格键触发，见 toggle()。
})();
