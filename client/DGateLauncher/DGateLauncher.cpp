// DGateLauncher.cpp : 应用入口（MFC 静态链接 / Unicode / Win32）
#include "stdafx.h"
#include "DGateLauncher.h"
#include "Logger.h"

#include <gdiplus.h>

BEGIN_MESSAGE_MAP(CDGateApp, CWinApp)
END_MESSAGE_MAP()

CDGateApp theApp;

BOOL CDGateApp::InitInstance()
{
    INITCOMMONCONTROLSEX InitCtrls;
    InitCtrls.dwSize = sizeof(INITCOMMONCONTROLSEX);
    InitCtrls.dwICC = ICC_WIN95_CLASSES;
    InitCommonControlsEx(&InitCtrls);

    CWinApp::InitInstance();

    // 主题绘制需要 GDI+（渐变/圆角）；gdiplus.dll 为系统自带，无需分发
    Gdiplus::GdiplusStartupInput gdiplusStartupInput;
    if (Gdiplus::GdiplusStartup(&m_gdiplusToken, &gdiplusStartupInput, nullptr)
        != Gdiplus::Ok)
        m_gdiplusToken = 0;

    Logger::Init();

    CMainDialog dlg;
    m_pMainWnd = &dlg;
    INT_PTR nResponse = dlg.DoModal();
    if (nResponse == IDRETRY) {
        // 拉起游戏失败：重新显示主界面以便用户处理
        CMainDialog retry;
        m_pMainWnd = &retry;
        retry.DoModal();
    }

    // 由于对话框已关闭，返回 FALSE 以便退出应用程序，
    // 而不是启动应用程序的消息泵。
    return FALSE;
}

int CDGateApp::ExitInstance()
{
    if (m_gdiplusToken)
        Gdiplus::GdiplusShutdown(m_gdiplusToken);
    return CWinApp::ExitInstance();
}
