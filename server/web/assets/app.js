/* DGate 管理后台（Vue 3，无构建，全站 AJAX） */
(function () {
  'use strict';

  // 统一 AJAX 封装：JSON 请求 + 会话失效自动回到登录页
  async function api(method, url, body) {
    const opts = { method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (res.status === 401 && !url.endsWith('/api/admin/login')) {
      window.App && window.App.handleUnauthorized();
      throw new Error((data && data.message) || '登录已过期');
    }
    if (!res.ok || !data || data.code !== 0) {
      throw new Error((data && data.message) || ('请求失败（HTTP ' + res.status + '）'));
    }
    return data;
  }

  const Pager = {
    name: 'Pager',
    props: { page: Number, pageSize: Number, total: Number },
    emits: ['go'],
    computed: {
      pages: function () {
        return Math.max(1, Math.ceil((this.total || 0) / (this.pageSize || 20)));
      }
    },
    template:
      '<div class="pager" v-if="total !== null && total > 0">' +
      '  <button class="btn-ghost" :disabled="page <= 1" @click="$emit(\'go\', 1)">首页</button>' +
      '  <button class="btn-ghost" :disabled="page <= 1" @click="$emit(\'go\', page - 1)">上一页</button>' +
      '  <span class="mono">{{ page }} / {{ pages }}</span>' +
      '  <button class="btn-ghost" :disabled="page >= pages" @click="$emit(\'go\', page + 1)">下一页</button>' +
      '  <button class="btn-ghost" :disabled="page >= pages" @click="$emit(\'go\', pages)">末页</button>' +
      '</div>'
  };

  const app = Vue.createApp({
    components: { pager: Pager },
    data: function () {
      return {
        view: 'loading',          // loading | login | main
        tab: 'server',            // 登录后默认进入游戏服务页
        sidebarOpen: false,       // 小屏抽屉侧栏开关（响应式）
        loginUsername: '', loginPassword: '', loginBusy: false, loginError: '',
        sessionInfo: { logged_in: false, username: '', is_super: false },

        // 用户管理
        users: [], userTotal: null, userPage: 1, userPageSize: 20,
        userSearch: '', userLoading: false,

        // 审计
        auditItems: [], auditTotal: null, auditPage: 1, auditPageSize: 20,
        auditAction: '', auditLoading: false,
        auditActions: ['register', 'login', 'change_password', 'recharge',
                       'reset_password', 'admin_login', 'admin_password_change',
                       'admin_create', 'admin_delete', 'server_start',
                       'server_stop', 'game_upgrade', 'mail_items_refresh',
                       'mail_send', 'key_generate', 'key_download',
                       'baseline_reset', 'settings_update'],

        // 充值 / 重置
        rechargeTarget: null, rechargeAmount: '', rechargeBusy: false,
        resetResult: null,

        // 管理员配置（仅超管）
        adminItems: [], adminBusy: false,
        adminModal: { visible: false },
        newAdminName: '', newAdminPass: '',

        // 通用确认对话框（替代 window.confirm）
        confirmBox: { visible: false, title: '', text: '', danger: false,
                      resolve: null },

        // 修改密码（toast 浮层，入口在侧栏底部）
        pwdModal: { visible: false },
        cpOld: '', cpNew: '', cpConfirm: '', cpBusy: false,

        // 服务器控制
        serverStatus: { running: false, pids: [] },
        serverStatusLoading: false, serverBusy: false, serverLoaded: false,
        serverStarting: false, serverStartTimer: null,

        // 游戏升级
        upInfo: null, upBusy: false, upProgress: 0, upProcessing: false,
        baselineBusy: false,

        // 认证密钥
        keyInfo: null, keyBusy: false,

        // 参数设置
        settingsInfo: null, settingsCera: '', settingsBusy: false,

        // 系统健康（后端常驻采样每 2 秒；本页轮询展示）
        healthData: null, healthTimer: null,

        // 邮件发送
        mailMode: 'single',
        mailCharacName: '', mailCharacNo: null,
        characCandidates: [], characSearchTimer: null,
        mailRecipients: null,
        mailSender: '', mailText: '', mailGold: '',
        mailItems: [], mailItemsTotal: null, mailItemsInfo: null,
        mailItemsBusy: false, mailItemsLoaded: false,
        mailItemSearch: '', mailItemCandidates: [],
        mailAttach: [], mailBusy: false,
        mailState: { building: false, ready: false, error: null,
                     count: null, pvf_mtime: null, pvf_size: null },
        mailStateTimer: null,

        toast: null, toastTimer: null
      };
    },
    created: function () {
      window.App = this;
      this.checkSession();
    },
    computed: {
      pageTitle: function () {
        return { users: '用户管理', audit: '审计信息', server: '游戏服务',
                 mail: '邮件发送', admins: '管理员配置' }[this.tab] || '';
      },
      pageDesc: function () {
        return {
          users: '查看全部账号，支持搜索、点券充值与密码重置',
          audit: '全部用户与后台行为记录',
          server: '游戏服务器进程控制',
          mail: '发送邮件给指定角色或全服，可附金币与道具',
          admins: '添加与删除后台管理员（仅超级管理员）'
        }[this.tab] || '';
      }
    },
    methods: {
      // ---------- 会话 ----------
      checkSession: async function () {
        try {
          const r = await api('GET', '/api/admin/session');
          this.sessionInfo = r.data;
          this.view = r.data.logged_in ? 'main' : 'login';
          if (this.view === 'main') this.switchTab('server');
        } catch (e) {
          this.view = 'login';
        }
      },
      handleUnauthorized: function () {
        this.view = 'login';
        this.sessionInfo = { logged_in: false, username: '', is_super: false };
        this.loginPassword = '';
        this.stopHealthPoll();
      },
      doLogin: async function () {
        this.loginBusy = true; this.loginError = '';
        try {
          const r = await api('POST', '/api/admin/login',
                              { username: this.loginUsername,
                                password: this.loginPassword });
          this.sessionInfo = { logged_in: true,
                               username: r.data.username,
                               is_super: r.data.is_super };
          this.view = 'main';
          this.loginPassword = '';
          this.switchTab('server');
        } catch (e) {
          this.loginError = e.message;
        } finally {
          this.loginBusy = false;
        }
      },
      doLogout: async function () {
        try { await api('POST', '/api/admin/logout'); } catch (e) { /* 忽略 */ }
        this.sessionInfo = { logged_in: false, username: '', is_super: false };
        this.loginUsername = '';
        this.loginPassword = '';
        this.view = 'login';
        this.stopHealthPoll();
      },

      // ---------- 通用 ----------
      switchTab: function (t) {
        this.tab = t;
        this.sidebarOpen = false;   // 小屏抽屉选中后自动收起
        if (t === 'users' && this.users.length === 0 && this.userTotal === null) this.loadUsers(1);
        if (t === 'audit' && this.auditItems.length === 0 && this.auditTotal === null) this.loadAudit(1);
        if (t === 'admins' && this.sessionInfo.is_super) this.loadAdmins();
        if (t === 'server') {
          if (!this.serverLoaded) {
            this.loadServerStatus(); this.loadUpgradeInfo(); this.loadKeysInfo();
            this.loadMailState(false); this.loadSettings();
          }
          this.startHealthPoll();   // 仅停留本页时轮询；离开即停（后端采样常驻）
        } else {
          this.stopHealthPoll();
        }
        if (t === 'mail') {
          this.loadMailState(true);   // 就绪后自动拉取清单
          this.refreshMailRecipients();
        }
      },
      showToast: function (text, type) {
        const self = this;
        this.toast = { text: text, type: type || 'ok' };
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(function () { self.toast = null; }, 2600);
      },
      confirmAsync: function (title, text, danger) {
        const self = this;
        return new Promise(function (resolve) {
          self.confirmBox = { visible: true, title: title, text: text,
                              danger: !!danger, resolve: resolve };
        });
      },
      askConfirm: function (title, text, action, danger) {
        const self = this;
        this.confirmAsync(title, text, danger)
          .then(function (ok) { if (ok) action(); });
      },
      onConfirmOk: function () {
        const resolve = this.confirmBox.resolve;
        this.confirmBox.visible = false;
        if (resolve) resolve(true);
      },
      onConfirmCancel: function () {
        const resolve = this.confirmBox.resolve;
        this.confirmBox.visible = false;
        if (resolve) resolve(false);
      },
      actionLabel: function (a) {
        const map = {
          register: '注册', login: '登录', change_password: '修改密码',
          recharge: '充值', reset_password: '重置密码', admin_login: '后台登录',
          admin_password_change: '修改后台密码',
          admin_create: '添加管理员', admin_delete: '删除管理员',
          server_start: '启动服务器', server_stop: '停止服务器',
          game_upgrade: '游戏升级', mail_items_refresh: '重建道具清单',
          mail_send: '发送邮件', key_generate: '生成认证密钥',
          key_download: '下载私钥', baseline_reset: '重设基线版本',
          settings_update: '修改参数',
          user_ban: '封号', user_unban: '解封'
        };
        return map[a] || a;
      },

      // ---------- 用户管理 ----------
      loadUsers: async function (page) {
        const self = this;
        this.userLoading = true;
        this.userPage = page || this.userPage;
        try {
          const qs = new URLSearchParams({
            page: this.userPage, page_size: this.userPageSize
          });
          if (this.userSearch) qs.set('search', this.userSearch);
          const r = await api('GET', '/api/admin/users?' + qs.toString());
          self.users = r.data.items;
          self.userTotal = r.data.total;
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          self.userLoading = false;
        }
      },
      openRecharge: function (u) {
        this.rechargeTarget = u;
        this.rechargeAmount = '';
      },
      doRecharge: async function () {
        const amount = Number(this.rechargeAmount);
        if (!Number.isInteger(amount) || amount === 0) {
          this.showToast('请输入非零整数', 'err');
          return;
        }
        this.rechargeBusy = true;
        try {
          const r = await api('POST', '/api/admin/recharge',
                              { uid: this.rechargeTarget.uid, amount: amount });
          this.showToast('充值成功，当前点券 ' + r.data.cera.toLocaleString());
          this.rechargeTarget = null;
          this.loadUsers(this.userPage);
        } catch (e) {
          this.showToast(e.message, 'err');
        } finally {
          this.rechargeBusy = false;
        }
      },
      openReset: function (u) {
        const self = this;
        this.askConfirm('确认重置密码', '确认重置用户 ' + u.accountname + ' 的密码？',
                        function () {
                          api('POST', '/api/admin/reset_password', { uid: u.uid })
                            .then(function (r) { self.resetResult = r.data;
                                                 self.loadUsers(self.userPage); })
                            .catch(function (e) { self.showToast(e.message, 'err'); });
                        });
      },
      doBan: function (u, banning) {
        const self = this;
        const title = banning ? '确认封号' : '确认解封';
        const text = banning
          ? '封停用户 ' + u.accountname + '？封号后该用户将无法登录游戏。'
          : '解封用户 ' + u.accountname + '？解封后该用户可重新登录游戏。';
        this.askConfirm(title, text,
                        async function () {
                          try {
                            const r = await api('POST',
                                                banning ? '/api/admin/ban'
                                                        : '/api/admin/unban',
                                                { uid: u.uid });
                            self.showToast(r.message || (banning ? '已封号' : '已解封'));
                            self.loadUsers(self.userPage);
                          } catch (e) {
                            self.showToast(e.message, 'err');
                          }
                        }, banning);
      },
      copyPassword: function () {
        const self = this;
        const text = this.resetResult.new_password;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(
            function () { self.showToast('已复制到剪贴板'); },
            function () { self.fallbackCopy(text); });
        } else {
          this.fallbackCopy(text);
        }
      },
      fallbackCopy: function (text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); this.showToast('已复制到剪贴板'); }
        catch (e) { this.showToast('复制失败，请手动复制', 'err'); }
        document.body.removeChild(ta);
      },

      // ---------- 审计 ----------
      loadAudit: async function (page) {
        const self = this;
        this.auditLoading = true;
        this.auditPage = page || this.auditPage;
        try {
          const qs = new URLSearchParams({
            page: this.auditPage, page_size: this.auditPageSize
          });
          if (this.auditAction) qs.set('action', this.auditAction);
          const r = await api('GET', '/api/admin/audit?' + qs.toString());
          self.auditItems = r.data.items;
          self.auditTotal = r.data.total;
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          self.auditLoading = false;
        }
      },

      // ---------- 服务器控制 ----------
      loadServerStatus: async function () {
        const self = this;
        this.serverStatusLoading = true;
        try {
          const r = await api('GET', '/api/admin/server/status');
          self.serverStatus = r.data;
          self.serverLoaded = true;
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          self.serverStatusLoading = false;
        }
      },
      doServerStart: function () {
        const self = this;
        if (this.mailState.building) {
          this.showToast('道具清单正在重建，请稍候再启动服务器', 'err');
          return;
        }
        if (this.serverStatus.running) {
          this.showToast('服务器运行中，请先停止后再启动', 'err');
          return;
        }
        if (this.serverStarting || this.serverBusy) return;
        this.askConfirm('确认启动游戏服务器', '全套服务约需数十秒拉起。',
                        function () { self._serverStartConfirmed(); });
      },
      _serverStartConfirmed: async function () {
        const self = this;
        this.serverBusy = true;
        let started = false;
        try {
          await api('POST', '/api/admin/server/start');
          started = true;
          self.showToast('已发起启动，等待服务器进程拉起…');
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          self.serverBusy = false;
        }
        if (!started) return;
        // 锁定启动按钮，直至检测到服务器进程成功开启
        self.serverStarting = true;
        const deadline = Date.now() + 120000;
        const poll = async function () {
          if (!self.serverStarting) return;
          try {
            const r = await api('GET', '/api/admin/server/status');
            self.serverStatus = r.data;
            self.serverLoaded = true;
            if (r.data.running) {
              self.serverStarting = false;
              self.showToast('服务器已启动');
              return;
            }
          } catch (e) { /* 忽略单次状态查询失败 */ }
          if (Date.now() >= deadline) {
            self.serverStarting = false;
            self.showToast('启动检测超时，请手动刷新状态确认', 'err');
            return;
          }
          self.serverStartTimer = setTimeout(poll, 3000);
        };
        setTimeout(poll, 3000);   // run 脚本先清理旧进程，稍候再开始检测
      },
      doServerStop: function () {
        const self = this;
        if (this.serverStarting) {
          this.showToast('服务器正在启动，请稍候', 'err');
          return;
        }
        this.askConfirm('确认停止游戏服务器', '所有在线玩家将断开。',
                        function () { self._serverStopConfirmed(); }, true);
      },
      _serverStopConfirmed: async function () {
        const self = this;
        this.serverBusy = true;
        try {
          await api('POST', '/api/admin/server/stop');
          self.showToast('停止脚本已执行');
          setTimeout(function () { self.loadServerStatus(); }, 1500);
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          this.serverBusy = false;
        }
      },

      // ---------- 游戏升级 ----------
      loadUpgradeInfo: async function () {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/upgrade');
          self.upInfo = r.data;
        } catch (e) { /* 信息加载失败不打断页面 */ }
      },
      onUpFileChange: function (ev) {
        const files = ev.target.files;
        const file = files && files.length ? files[0] : null;
        ev.target.value = '';            // 允许连续选择同一文件
        if (!file) return;
        if (this.serverStatus.running) {
          this.showToast('服务器运行中，请先停止后再升级游戏', 'err');
          return;
        }
        const self = this;
        this.askConfirm('确认上传补丁并执行游戏升级',
                        'updates 目录中同名文件将被覆盖；补丁若包含 Script.pvf '
                        + '将同步覆盖到游戏服务器。',
                        function () { self.uploadUpgrade(file); });
      },
      uploadUpgrade: function (file) {
        const self = this;
        this.upBusy = true;
        this.upProcessing = false;
        this.upProgress = 0;
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/admin/upgrade');
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable) {
            self.upProgress = Math.round(e.loaded * 100 / e.total);
            if (self.upProgress >= 100) {
              // 上传完成，等待服务端解压打包
              self.upProcessing = true;
            }
          }
        };
        xhr.onload = function () {
          self.upBusy = false;
          self.upProcessing = false;
          let body = {};
          try { body = JSON.parse(xhr.responseText); } catch (err) { /* 忽略 */ }
          if (xhr.status === 200 && body.code === 0) {
            self.showToast(body.message || '游戏升级成功');
          } else {
            // 含运行中二次检查失败的 409 报错
            self.showToast(body.message || ('上传失败（HTTP ' + xhr.status + '）'), 'err');
          }
          self.loadServerStatus();
          self.loadUpgradeInfo();
          self.loadMailState(false);   // 补丁含 PVF 时清单开始重建
        };
        xhr.onerror = function () {
          self.upBusy = false;
          self.upProcessing = false;
          self.showToast('网络错误，上传失败', 'err');
        };
        const form = new FormData();
        form.append('file', file, file.name);
        xhr.send(form);
      },

      doSetBaseline: function () {
        const self = this;
        const ver = this.upInfo ? this.upInfo.current : '';
        // 双重确认（防误操作）：明确提示影响后再次确认
        this.askConfirm('将当前版本设置为基线版本',
                        '此操作将使版本号低于当前版本号的用户无法登录游戏。'
                        + '请注意提供对应基线版本的游戏客户端。',
                        function () {
                          self.askConfirm('再次确认',
                                          '确认将基线版本重设为 v' + ver
                                          + '？updates 目录将被清空，此操作不可撤销。',
                                          async function () {
                                            self.baselineBusy = true;
                                            try {
                                              const r = await api('POST',
                                                '/api/admin/upgrade/baseline');
                                              self.showToast(r.message || '基线版本已重设');
                                              self.loadUpgradeInfo();
                                            } catch (e) {
                                              self.showToast(e.message, 'err');
                                            } finally {
                                              self.baselineBusy = false;
                                            }
                                          }, true);
                        }, true);
      },

      // ---------- 认证密钥 ----------
      loadKeysInfo: async function () {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/keys');
          self.keyInfo = r.data;
        } catch (e) { /* 信息加载失败不打断页面 */ }
      },
      doGenKeys: function () {
        const self = this;
        this.askConfirm('确认生成新密钥对',
                        '生成后旧密钥立即作废：私钥将立即用于登录签发，'
                        + '游戏目录公钥将被覆盖（原文件备份为 .bak）。',
                        async function () {
                          self.keyBusy = true;
                          try {
                            await api('POST', '/api/admin/keys/generate');
                            self.showToast('新密钥对已生成并启用');
                            self.loadKeysInfo();
                          } catch (e) {
                            self.showToast(e.message, 'err');
                          } finally {
                            self.keyBusy = false;
                          }
                        }, true);
      },
      downloadKey: function (which) {
        const url = which === 'private' ? '/api/admin/keys/private.pem'
                                        : '/api/admin/keys/public.pem';
        const a = document.createElement('a');
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      },
      downloadPrivateKey: function () {
        const self = this;
        // 双重确认（防泄漏）：两道危险确认都通过才下载
        this.askConfirm('下载私钥',
                        '私钥是系统登录签名的根密钥，请确认下载用途。',
                        function () {
                          self.askConfirm('再次确认',
                                          '私钥一旦泄露，任何人都可以伪造游戏登录。'
                                          + '确认继续下载？',
                                          function () {
                                            self.downloadKey('private');
                                          }, true);
                        }, true);
      },

      // ---------- 参数设置 ----------
      loadSettings: async function () {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/settings');
          self.settingsInfo = r.data;
          self.settingsCera = String(r.data.register_init_cera);
        } catch (e) {
          self.showToast(e.message, 'err');
        }
      },
      doSaveSettings: async function () {
        const self = this;
        const v = Number(this.settingsCera);
        if (!Number.isInteger(v) || v < 0 || v > 2147483647) {
          this.showToast('注册赠送点券需为 0~2147483647 的整数', 'err');
          return;
        }
        if (!this.settingsInfo) {
          this.showToast('参数尚未加载，请稍后重试', 'err');
          return;
        }
        const old = this.settingsInfo.register_init_cera;
        if (v === old) {
          this.showToast('值未变化，无需保存');
          return;
        }
        const ok = await this.confirmAsync(
          '确认修改参数',
          '注册赠送点券将从 ' + old.toLocaleString() + ' 改为 '
          + v.toLocaleString() + '，保存后立即对新注册用户生效。');
        if (!ok) return;
        this.settingsBusy = true;
        try {
          const r = await api('POST', '/api/admin/settings',
                              { register_init_cera: v });
          self.showToast(r.message || '参数已保存');
          await self.loadSettings();
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          this.settingsBusy = false;
        }
      },

      // ---------- 系统健康 ----------
      startHealthPoll: function () {
        const self = this;
        if (this.healthTimer) return;
        this.loadHealth();
        this.healthTimer = setInterval(function () { self.loadHealth(); }, 2000);
      },
      stopHealthPoll: function () {
        if (this.healthTimer) {
          clearInterval(this.healthTimer);
          this.healthTimer = null;
        }
      },
      loadHealth: async function () {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/syshealth');
          self.healthData = r.data;
        } catch (e) { /* 单次读取失败不打断，下个周期重试 */ }
      },
      fmtPct: function (v) {
        return (v === null || v === undefined) ? '—' : v.toFixed(1) + '%';
      },
      fmtGB: function (v) {
        return (v === null || v === undefined) ? '—' : (v / 1073741824).toFixed(1) + ' GB';
      },
      fmtMB: function (v) {
        return (v === null || v === undefined) ? '—' : Math.round(v / 1048576) + ' MB';
      },
      // 健康分级：<50% 绿 / 50~80% 黄 / >=80% 红（按使用率，仅当前值着色）
      healthLevel: function (pct) {
        if (pct === null || pct === undefined || isNaN(pct)) return '';
        if (pct >= 80) return 'health-crit';
        if (pct >= 50) return 'health-warn';
        return 'health-ok';
      },
      cpuLevel: function () {
        const d = this.healthData;
        return this.healthLevel(d ? d.cpu_percent : null);
      },
      memLevel: function () {
        const d = this.healthData;
        if (!d || d.mem_used === null || d.mem_used === undefined || !d.mem_total) return '';
        return this.healthLevel(d.mem_used * 100 / d.mem_total);
      },
      diskLevel: function () {
        const d = this.healthData;
        if (!d || d.disk_free === null || d.disk_free === undefined || !d.disk_total) return '';
        return this.healthLevel((d.disk_total - d.disk_free) * 100 / d.disk_total);
      },

      // ---------- 邮件发送 ----------
      loadMailState: async function (chainItems) {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/mail/items/state');
          const wasBuilding = this.mailState.building;
          self.mailState = r.data;
          if (r.data.building) {
            clearTimeout(self.mailStateTimer);
            self.mailStateTimer = setTimeout(function () {
              self.loadMailState(chainItems);
            }, 2000);
          } else if (r.data.ready && chainItems && !self.mailItemsLoaded) {
            // 进入页面且清单就绪但尚未拉取（含构建刚完成的情况）
            if (wasBuilding) self.showToast('道具清单重建完成');
            self.loadMailItems();
          }
        } catch (e) { /* 状态获取失败不打断页面 */ }
      },
      loadMailItems: async function () {
        const self = this;
        if (this.mailState.building) return;
        if (!this.mailState.ready) { this.loadMailState(true); return; }
        this.mailItemsBusy = true;
        try {
          const r = await api('GET', '/api/admin/mail/items');
          self.applyMailItems(r.data);
        } catch (e) {
          self.showToast(e.message, 'err');
          self.loadMailState(false);
        } finally {
          self.mailItemsBusy = false;
        }
      },
      applyMailItems: function (data) {
        this.mailItems = data.items;
        this.mailItemsTotal = data.total;
        this.mailItemsInfo = { count: data.count, pvf_mtime: data.pvf_mtime,
                               pvf_size: data.pvf_size };
        this.mailItemsLoaded = true;
        this.filterMailItems();
      },
      refreshMailRecipients: async function () {
        try {
          const r = await api('GET', '/api/admin/mail/recipients?mode=all');
          this.mailRecipients = r.data.count;
        } catch (e) { /* 数量获取失败不提示，发送时后端会再校验 */ }
      },
      searchCharac: function (ev) {
        const self = this;
        if (ev && ev.isComposing) return;   // 输入法组合中不搜索
        this.mailCharacNo = null;
        clearTimeout(this.characSearchTimer);
        const kw = (ev ? ev.target.value : this.mailCharacName).trim();
        if (!kw) { this.characCandidates = []; return; }
        this.characSearchTimer = setTimeout(async function () {
          try {
            const r = await api('GET', '/api/admin/mail/characters?search='
                                + encodeURIComponent(kw));
            self.characCandidates = r.data.items;
          } catch (e) { /* 搜索失败静默 */ }
        }, 300);
      },
      pickCharac: function (c) {
        this.mailCharacName = c.charac_name;
        this.mailCharacNo = c.charac_no;
        this.characCandidates = [];
      },
      filterMailItems: function () {
        const kw = this.mailItemSearch.toLowerCase();
        if (!kw) { this.mailItemCandidates = []; return; }
        const picked = {};
        this.mailAttach.forEach(function (a) { picked[a.id] = 1; });
        const hits = this.mailItems.filter(function (it) {
          if (picked[it.id]) return false;
          return it.name.toLowerCase().indexOf(kw) >= 0
                 || it.name_tc.toLowerCase().indexOf(kw) >= 0
                 || String(it.id).indexOf(kw) === 0;
        });
        this.mailItemCandidates = hits.slice(0, 20);
      },
      addMailItem: function (it) {
        if (this.mailAttach.length >= 10) {
          this.showToast('附件最多 10 种道具', 'err');
          return;
        }
        this.mailAttach.push({ id: it.id, name: it.name, name_tc: it.name_tc,
                               count: '1' });
        this.mailItemSearch = '';
        this.mailItemCandidates = [];
      },
      doMailSend: async function () {
        const self = this;
        if (this.mailState.building) {
          this.showToast('道具清单正在重建，请稍候', 'err');
          return;
        }
        const text = this.mailText.trim();
        const gold = Number(this.mailGold || 0);
        if (!Number.isInteger(gold) || gold < 0) {
          this.showToast('金币需为非负整数', 'err');
          return;
        }
        const items = [];
        for (let i = 0; i < this.mailAttach.length; i++) {
          const a = this.mailAttach[i];
          const count = Number(a.count);
          if (!Number.isInteger(count) || count <= 0) {
            this.showToast('附件「' + (a.name || a.name_tc) + '」数量需为正整数', 'err');
            return;
          }
          items.push({ id: a.id, count: count });
        }
        if (!text && items.length === 0 && gold === 0) {
          this.showToast('请填写正文或添加金币/附件', 'err');
          return;
        }
        if (this.mailMode === 'single' && !this.mailCharacNo) {
          this.showToast('请从搜索结果中选择收件角色', 'err');
          return;
        }

        let summary = '确认发送邮件？\n';
        if (this.mailMode === 'single') {
          summary += '收件角色：' + this.mailCharacName + '\n';
        } else {
          summary += '收件范围：全服 ' + this.mailRecipients + ' 个角色\n';
        }
        summary += '发件人：' + (this.mailSender || '系统')
                 + '\n金币：' + gold
                 + '\n附件：' + (items.length
                     ? items.map(function (a) {
                         const it = self.mailItems.filter(
                           function (x) { return x.id === a.id; })[0];
                         return (it ? (it.name || it.name_tc) : ('id ' + a.id))
                                + '×' + a.count;
                       }).join('、')
                     : '无');
        if (this.mailMode === 'all') {
          summary += '\n\n将向全服每个角色各投递一封邮件，确认执行？';
        }
        if (!(await this.confirmAsync('确认发送邮件', summary))) return;

        this.mailBusy = true;
        try {
          let expected = null;
          if (this.mailMode === 'all') {
            const rc = await api('GET', '/api/admin/mail/recipients?mode=all');
            expected = rc.data.count;
            if (expected !== this.mailRecipients) {
              this.mailRecipients = expected;
              const ok2 = await this.confirmAsync(
                '全服角色数已变化',
                '全服角色数已变为 ' + expected + '，仍确认发送？', true);
              if (!ok2) return;
            }
          }
          const body = { mode: this.mailMode, sender: this.mailSender || '系统',
                         text: text, gold: gold, items: items };
          if (this.mailMode === 'single') {
            body.character = this.mailCharacName;
          } else {
            body.expected_count = expected;
          }
          const r = await api('POST', '/api/admin/mail/send', body);
          self.showToast(r.message);
          self.mailText = ''; self.mailGold = ''; self.mailAttach = [];
          self.mailItemSearch = ''; self.mailItemCandidates = [];
          self.mailCharacName = ''; self.mailCharacNo = null;
          self.characCandidates = [];
          if (self.mailMode === 'all') self.refreshMailRecipients();
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          self.mailBusy = false;
        }
      },

      // ---------- 管理员配置（仅超管） ----------
      loadAdmins: async function () {
        const self = this;
        try {
          const r = await api('GET', '/api/admin/admins');
          self.adminItems = r.data.items;
        } catch (e) {
          self.showToast(e.message, 'err');
        }
      },
      openAdminModal: function () {
        this.newAdminName = '';
        this.newAdminPass = '';
        this.adminModal = { visible: true };
      },
      doAddAdmin: async function () {
        const self = this;
        this.adminBusy = true;
        try {
          await api('POST', '/api/admin/admins',
                    { username: this.newAdminName,
                      password: this.newAdminPass });
          self.showToast('管理员已添加');
          self.newAdminName = self.newAdminPass = '';
          self.adminModal.visible = false;
          self.loadAdmins();
        } catch (e) {
          self.showToast(e.message, 'err');
        } finally {
          this.adminBusy = false;
        }
      },
      doDeleteAdmin: function (a) {
        const self = this;
        this.askConfirm('确认删除管理员',
                        '将删除管理员 ' + a.username + '，该操作不可恢复。',
                        async function () {
                          self.adminBusy = true;
                          try {
                            await api('POST', '/api/admin/admins/delete',
                                      { username: a.username });
                            self.showToast('管理员已删除');
                            self.loadAdmins();
                          } catch (e) {
                            self.showToast(e.message, 'err');
                          } finally {
                            self.adminBusy = false;
                          }
                        }, true);
      },

      // ---------- 修改密码（toast 浮层） ----------
      openPwdToast: function () {
        this.cpOld = this.cpNew = this.cpConfirm = '';
        this.pwdModal = { visible: true };
        this.sidebarOpen = false;   // 小屏点击后收起抽屉
      },
      doChangePassword: async function () {
        if (this.cpNew !== this.cpConfirm) {
          this.showToast('两次输入的新密码不一致', 'err');
          return;
        }
        this.cpBusy = true;
        try {
          await api('POST', '/api/admin/change_password',
                    { old_password: this.cpOld, new_password: this.cpNew });
          this.showToast('密码已修改');
          this.cpOld = this.cpNew = this.cpConfirm = '';
          this.pwdModal.visible = false;
        } catch (e) {
          this.showToast(e.message, 'err');
        } finally {
          this.cpBusy = false;
        }
      }
    }
  });

  app.mount('#app');
})();
