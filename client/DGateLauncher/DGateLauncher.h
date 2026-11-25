// DGateLauncher.h : 应用主头
#pragma once

#ifndef __AFXWIN_H__
    #error "在包含此文件之前包含 'stdafx.h' 以生成 PCH 文件"
#endif

#include "MainDialog.h"

class CDGateApp : public CWinApp {
public:
    CDGateApp() = default;

public:
    virtual BOOL InitInstance() override;
    virtual int ExitInstance() override;

    DECLARE_MESSAGE_MAP()

private:
    ULONG_PTR m_gdiplusToken = 0;   // 主题绘制所用 GDI+ 会话令牌
};
