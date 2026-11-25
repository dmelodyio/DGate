// UpdateDialog.h : 游戏更新进度对话框（下载 + 解压，均在工作线程执行）
#pragma once

#include "Theme.h"

// 自定义消息：工作线程 -> 对话框（仅通过 HWND PostMessage 通信）
#define DGATE_WM_UPD_PROGRESS   (WM_APP + 1)   // WPARAM = 下载百分比 0-100
#define DGATE_WM_UPD_PHASE      (WM_APP + 2)   // WPARAM = 1 安装阶段
#define DGATE_WM_UPD_DONE       (WM_APP + 3)   // WPARAM = 1 成功 0 失败

class CUpdateDialog : public CThemeDialog {
    DECLARE_MESSAGE_MAP()

public:
    explicit CUpdateDialog(int targetVersion, const CStringW& serverUrl,
                           CWnd* pParent = nullptr);

    enum { IDD = IDD_UPDATE };

    CStringW m_error;          // 失败原因（DoModal 返回 IDCANCEL 后由调用方弹窗）
    int m_extractedFiles = 0;  // 成功时解压出的文件数

protected:
    virtual BOOL OnInitDialog() override;
    virtual CStringW HeaderCaption() const override { return L"游戏更新"; }
    virtual CStringW WindowText() const override { return L"DGate 更新"; }
    virtual void GetThemeEditIds(CUIntArray& ids) const override {}
    virtual bool HasCloseButton() const override { return false; }

    afx_msg void OnPaint();
    afx_msg void OnClose();                      // 更新不可中断：忽略关闭
    afx_msg LRESULT OnUpdProgress(WPARAM wParam, LPARAM lParam);
    afx_msg LRESULT OnUpdPhase(WPARAM wParam, LPARAM lParam);
    afx_msg LRESULT OnUpdDone(WPARAM wParam, LPARAM lParam);

private:
    static UINT __cdecl WorkerThread(LPVOID param);

    int m_targetVersion;
    CStringW m_serverUrl;
    int m_percent = 0;
    bool m_installing = false;
};
