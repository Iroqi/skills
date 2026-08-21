import sys
sys.path.insert(0, r'c:\Users\Hopper\Documents\CODE\skills\content-to-video\scripts')
from _contracts import validate_segments_source

# Case 1: dialogue 是非列表真值（如字符串）且同时有 text —— 是否放行？
data = {"segments": [{"title": "t", "text": "正常文本。", "dialogue": "oops"}]}
try:
    validate_segments_source(data)
    print("Case1: 校验放行（漏洞确认）")
except ValueError as e:
    print("Case1: 校验拦截 ->", e)

# Case 2: 下游 build_from_structured 对该输入的行为
from build_from_structured import build_parts
try:
    build_parts(data)
    print("Case2: build_parts 正常")
except ValueError as e:
    print("Case2: ValueError ->", e)
except AttributeError as e:
    print("Case2: AttributeError 裸栈 ->", e)
