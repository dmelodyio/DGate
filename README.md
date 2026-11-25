# 某动作游戏运营套件（网关+网页GM工具+登录器三合一）

**警告**：
* 本项目以学习或研究该动作游戏服务端为目的创建并维护，请勿将本项目代码（或构建产物）用于任何商业运营行为！如用户因违规使用导致信誉与法律纠纷，作者概不负责！
* 作者不响应任何新增功能或改进需求，也不为任何实际运营活动提供技术支持。如发现bug请提交到Issues，作者将在复现后予以修复。
* 本项目不提供预编译包下载。请自行下载源码编译打包。

## 项目特性

本项目集合了该目标动作游戏运营所需的周边要素：
* 网关：游戏账号注册、登录与修改密码
* 网页GM工具：游戏服务器开关控制、用户管理、点券充值、游戏内邮件发送（支持携带附件）、封号等
* 登录器：登录后拉起游戏进程

相较其他同类工具的优点：
* 便利性：
    * 服务端一键部署（但游戏服务器需要自行安装）
* 功能
    * 直接在浏览器中运行GM工具：解决某些GM工具被报毒和缺少dll组件，导致无法正常使用的问题
    * 支持游戏升级：GM上传补丁后，所有玩家下次打开登录器时自动更新游戏客户端
    * 邮件功能支持发送最多10种道具，可通过名称及物品ID搜索选择（对应服务器当前pvf文件中的道具清单）
    * 支持向全部未删除角色发送邮件（服务器维护补偿？）
* 安全性
    * 登录器使用C++语言编写，降低杀软报毒概率
    * 部署游戏服务器时可限制game数据库账号仅本机访问

## 支持的游戏服务器部署方式

* 虚拟机服务端（如LK的S2A1虚拟机版本）
* 台服原生服务端（来自项目[weiguangchao/dof-install](https://github.com/weiguangchao/dof-install)）
* docker服务端（仅[1995chen/dnf](https://github.com/1995chen/dnf)项目的1995chen/dnf:centos7-2.1.9.fix1镜像）

## 环境要求

客户端编译：Windows系统 + Visual Studio 2022 （需要安装C++桌面开发负载与MFC支持）

服务端操作系统：
* CentOS 7.x
* Enterprise Linux 8+ (CentOS Stream、Alma Linux、Rocky Linux等)

## 项目编译打包

双击运行**build_dist.bat**，登录器编译与双端打包将自动完成。
在dist文件夹中可找到打包产物：
* client.zip：登录器及默认配置文件
* DGateServer.zip：服务端

## 服务端部署

### 虚拟机服务端

1. 如不清楚虚拟机的root密码（如LK的S2A1一键端），需要先停止游戏服务器后更改root密码
~~~
cd /root
./stop
passwd
~~~
2. 使用WinSCP等SFTP工具连接虚拟机
3. 解压缩打包产物dist\DGateServer.zip，之后将**解压得到的所有文件和子目录**（注意不要包含任何父目录）上传至/root目录
4. 使用Putty/SecureCRT等终端工具连接虚拟机，运行一键安装脚本
~~~
cd /root
chmod 755 *.sh dgate-*
./install-centos7.sh
~~~

### 台服原生服务端

1. 使用Putty/SecureCRT等终端工具连接服务器
2. 按[weiguangchao/dof-install](https://github.com/weiguangchao/dof-install)的说明安装好游戏服务端
3. 上传打包产物dist\DGateServer.zip到/root目录
4. 解压并运行一键安装脚本
~~~
yum install -y unzip
cd /root
unzip DGateServer.zip
chmod 755 *.sh dgate-*
./install-centos7.sh
~~~

如果需要绑定域名并启用HTTPS，运行一键部署脚本时需带上--domain命令行参数：
~~~
./install-centos7.sh --domain
~~~
脚本将引导输入绑定域名并自动申请Let's Encrypt证书。

### Docker服务端

1. 使用Putty/SecureCRT等终端工具连接服务器
2. 按[1995chen/dnf](https://github.com/1995chen/dnf)项目的说明安装好docker游戏服务端，创建容器并运行（注意当前仅支持1995chen/dnf:centos7-2.1.9.fix1镜像）
3. 上传打包产物dist\DGateServer.zip到/root目录
4. 解压并运行一键安装脚本
~~~
yum install -y unzip
cd /root
unzip DGateServer.zip
chmod 755 *.sh dgate-*
./install-rhel8.sh
~~~

如果需要绑定域名并启用SSL，运行一键部署脚本时需带上--domain命令行参数：
~~~
./install-rhel8.sh --domain
~~~
脚本将引导输入绑定域名并自动申请Let's Encrypt证书。

### 后台服务器管理

在/root目录下：
* 开启后台服务：./dgate-start
* 关闭后台服务：./dgate-stop
* 重启后台服务：./dgate-restart
* 查看后台服务状态：./dgate-status

## 登录器部署

将打包产物dist\client.zip解压到游戏客户端目录，并打开DGateServer.ini文件进行编辑：
~~~
[server]
; 设置为服务器后台的地址
url=http://10.65.237.242:5000

[inject]
; 如果客户端需要使用插件，在此进行注入
; 注入清单：逗号分隔，相对启动器所在目录，按给定顺序注入；留空则不注入任何 DLL
; 示例：dlls=ijl15.dll,Plugins\aaa.dll
dlls=ipproxy.dll

[update]
; 游戏版本。第一次部署时请固定设置为1。
version=1
~~~

## 参考

服务端进行pvf文件解析时参考了项目：[Zageku/DNF_pvf_python](https://github.com/Zageku/DNF_pvf_python)。感谢作者的辛勤付出！
