// GameLauncher.h : 拉起 dnf.exe 并按配置清单注入 DLL
#pragma once

class GameLauncher {
public:
    // 拉起 dnf.exe（参数为登录签名字符串），等待进程出现后按清单注入。
    // dllList 为 DGateLauncher.ini [inject] dlls 的原始条目（相对/绝对路径）。
    // 返回 true 表示进程已成功拉起；report 非空表示需要弹窗提示的失败详情。
    static bool LaunchAndInject(const CStringW& token,
                                const std::vector<CStringW>& dllList,
                                CString& report);

    // 启动器所在目录
    static CStringW ExeDir();

private:
    static bool InjectDll(HANDLE hProcess, const CStringW& dllPath, CString& error);
};
