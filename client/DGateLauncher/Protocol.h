// Protocol.h : 登录签名协议常量（与服务端 config.TOKEN_TEMPLATE 保持一致）
#pragma once

namespace Protocol {

// 基础字符串模板：%08x(UID 小写零填充 8 位) + "01"×32 + 尾串
const wchar_t* const kTail = L"55914510010403030101";
const int kOnePairCount = 32;

inline CStringW BuildTokenBaseString(unsigned int uid)
{
    CStringW s;
    s.Format(L"%08x", uid);
    for (int i = 0; i < kOnePairCount; ++i)
        s += L"01";
    s += kTail;
    return s;   // 共 92 字符
}

} // namespace Protocol
