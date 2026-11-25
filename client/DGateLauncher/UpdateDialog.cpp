// UpdateDialog.cpp : 游戏更新进度对话框（下载 + 解压，均在工作线程执行）
#include "stdafx.h"
#include "UpdateDialog.h"
#include "ApiClient.h"
#include "GameLauncher.h"
#include "Logger.h"
#include "MainDialog.h"
#include "ZipExtract.h"

#include <gdiplus.h>

using namespace DGateTheme;
using Gdiplus::REAL;

namespace {

// 工作线程任务参数（线程内自持有、自释放）
struct UpdateJob {
    CUpdateDialog* dlg;       // 仅供 PostMessage（HWND）使用
    int targetVersion;
    CStringW serverUrl;
};

Gdiplus::Color GColor(COLORREF c, BYTE alpha = 255)
{
    return Gdiplus::Color(alpha, GetRValue(c), GetGValue(c), GetBValue(c));
}

} // namespace

BEGIN_MESSAGE_MAP(CUpdateDialog, CThemeDialog)
    ON_WM_PAINT()
    ON_WM_CLOSE()
    ON_MESSAGE(DGATE_WM_UPD_PROGRESS, &CUpdateDialog::OnUpdProgress)
    ON_MESSAGE(DGATE_WM_UPD_PHASE, &CUpdateDialog::OnUpdPhase)
    ON_MESSAGE(DGATE_WM_UPD_DONE, &CUpdateDialog::OnUpdDone)
END_MESSAGE_MAP()

CUpdateDialog::CUpdateDialog(int targetVersion, const CStringW& serverUrl,
                             CWnd* pParent)
    : CThemeDialog(IDD, pParent)
    , m_targetVersion(targetVersion)
    , m_serverUrl(serverUrl)
{
}

BOOL CUpdateDialog::OnInitDialog()
{
    BOOL ok = CThemeDialog::OnInitDialog();
    CStringW start;
    start.Format(L"检测到新版本 v%d，开始自动更新", m_targetVersion);
    Logger::Write(start);
    auto* job = new UpdateJob{ this, m_targetVersion, m_serverUrl };
    AfxBeginThread(&CUpdateDialog::WorkerThread, job);
    return ok;
}

void CUpdateDialog::OnPaint()
{
    // 先交给基类绘制背景/标题条/描边，再叠加进度条与文字
    CThemeDialog::OnPaint();

    CClientDC dc(this);
    CRect rc;
    GetClientRect(&rc);
    const int headerH = HeaderHeight(*this);

    // —— 进度条（直角：EditBg 轨道 + Line 描边 + 金色渐变填充）——
    const int barLeft = 16;
    const int barTop = headerH + 22;
    const int barH = 14;
    CRect bar(barLeft, barTop, rc.right - 16, barTop + barH);
    dc.FillSolidRect(bar, EditBg);
    Gdiplus::Graphics g(dc.GetSafeHdc());
    if (m_percent > 0) {
        Gdiplus::RectF fill(REAL(bar.left), REAL(bar.top),
                            REAL(bar.Width() * m_percent / 100.0),
                            REAL(bar.Height()));
        Gdiplus::LinearGradientBrush brush(fill, GColor(Accent),
                                           GColor(AccentDark), 0.f);
        g.FillRectangle(&brush, fill);
    }
    dc.FrameRect(&bar, CBrush::FromHandle(Solid(Line)));

    // —— 阶段 + 百分比文字（功能反馈类状态文字）——
    CStringW text;
    if (m_installing)
        text = L"正在安装更新…";
    else
        text.Format(L"正在下载更新 %d%%", m_percent);
    dc.SetBkMode(TRANSPARENT);
    dc.SetTextColor(m_installing ? Muted : Text);
    CRect textRc(barLeft, bar.bottom + 8, rc.right - 16, bar.bottom + 28);
    dc.DrawTextW(text, textRc, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
}

void CUpdateDialog::OnClose()
{
    // 更新过程不可中断：忽略标题条关闭与系统关闭
}

LRESULT CUpdateDialog::OnUpdProgress(WPARAM wParam, LPARAM /*lParam*/)
{
    m_percent = static_cast<int>(wParam);
    Invalidate(FALSE);
    return 0;
}

LRESULT CUpdateDialog::OnUpdPhase(WPARAM wParam, LPARAM /*lParam*/)
{
    if (wParam == 1) {
        m_installing = true;
        Invalidate(FALSE);
    }
    return 0;
}

LRESULT CUpdateDialog::OnUpdDone(WPARAM wParam, LPARAM /*lParam*/)
{
    CStringW line;
    if (wParam)
        line.Format(L"游戏更新完成：基线版本 v%d，解压 %d 个文件",
                    m_targetVersion, m_extractedFiles);
    else
        line = L"游戏更新失败：" + m_error;
    Logger::Write(line);
    EndDialog(wParam ? IDOK : IDCANCEL);
    return 0;
}

UINT __cdecl CUpdateDialog::WorkerThread(LPVOID param)
{
    std::unique_ptr<UpdateJob> job(static_cast<UpdateJob*>(param));
    const HWND hwnd = job->dlg->GetSafeHwnd();

    // 1) 下载更新包到 %TEMP%
    wchar_t tmpDir[MAX_PATH] = {0};
    ::GetTempPathW(MAX_PATH, tmpDir);
    CStringW zipPath;
    zipPath.Format(L"%sDGateUpdate_%u.zip", tmpDir,
                   ::GetCurrentProcessId());

    CStringW error;
    auto progress = [hwnd](unsigned long long downloaded, unsigned long long total) {
        int pct = 0;
        if (total > 0)
            pct = static_cast<int>(downloaded * 100 / total);
        if (pct > 100)
            pct = 100;
        ::PostMessageW(hwnd, DGATE_WM_UPD_PROGRESS, static_cast<WPARAM>(pct), 0);
    };

    if (!ApiClient::DownloadFile(job->serverUrl, L"/api/client/updates.zip",
                                 zipPath, progress, error)) {
        job->dlg->m_error = error;
        ::PostMessageW(hwnd, DGATE_WM_UPD_DONE, 0, 0);
        return 0;
    }

    ::PostMessageW(hwnd, DGATE_WM_UPD_PHASE, 1, 0);

    // 2) 解压覆盖到启动器所在目录（即游戏目录）
    CStringW report;
    int extracted = 0;
    if (!ZipExtract::ExtractZip(zipPath, GameLauncher::ExeDir(), report,
                                extracted)) {
        job->dlg->m_error = report;
        ::PostMessageW(hwnd, DGATE_WM_UPD_DONE, 0, 0);
        return 0;
    }
    job->dlg->m_extractedFiles = extracted;

    // 3) 更新本地版本号并清理临时包
    CMainDialog::SaveLocalVersion(job->targetVersion);
    ::DeleteFileW(zipPath);

    ::PostMessageW(hwnd, DGATE_WM_UPD_DONE, 1, 0);
    return 0;
}
