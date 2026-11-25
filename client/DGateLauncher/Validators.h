// Validators.h : 客户端侧参数校验（与服务端规则一致，服务端仍为最终裁决）
#pragma once

// 用户名：6~16 位英文字母和数字
inline bool ValidUsername(const CStringW& s)
{
    const int len = s.GetLength();
    if (len < 6 || len > 16)
        return false;
    for (int i = 0; i < len; ++i)
    {
        wchar_t c = s[i];
        if (!((c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z') ||
              (c >= L'0' && c <= L'9')))
            return false;
    }
    return true;
}

// 密码：8~32 位可见 ASCII（英文字母、数字、符号）
inline bool ValidPassword(const CStringW& s)
{
    const int len = s.GetLength();
    if (len < 8 || len > 32)
        return false;
    for (int i = 0; i < len; ++i)
    {
        wchar_t c = s[i];
        if (c < 0x21 || c > 0x7e)
            return false;
    }
    return true;
}
