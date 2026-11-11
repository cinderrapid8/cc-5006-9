# Animation Previz Proxy Editing Workstation

动画预演代理剪辑工作站：高分辨率原片生成低清代理供 Qt 界面剪辑，正式导出时
按**内容哈希身份**重新链接原片，而不是按文件名替换。

## 核心保证

| 需求 | 实现方式 |
| --- | --- |
| 身份不依赖文件名 | 全文件 SHA-256 哈希作为主键；同名不同内容得到不同身份，重定位必须哈希精确匹配 |
| 记录代理生成参数与时间映射 | SQLite 持久化高度/CRF/preset/fps/帧数 + `FrameMap` JSON（帧率、帧数、起始偏移、变换类型） |
| 可精确回到原帧 | V1 仅接受**保持时间戳**的代理：同帧率、零偏移、帧数一一对应；帧率变化/帧数变化/上采样一律显式拒绝（`UnsupportedTransformError`） |
| 代理可信后才入库 | 结构校验 + 全帧解码完整性闸门 + 抽帧 MAE 内容比对 + 帧号往返验证 |
| 剪辑引用逻辑镜头与源区间 | clips 表存逻辑镜头哈希和源帧 `[in, out)`，切换预览清晰度不改变入出点 |
| 原片离线仍可剪代理 | 代理预览/时间线编辑不依赖原片在线 |
| 渲染前检查缺失来源 | `preflight()` 逐源哈希核验；任何原片缺失/被替换即阻断渲染并列出清单，绝不用代理冒充成片 |
| 文件搬迁 | 重定位对话框支持单文件选择与文件夹自动扫描，按哈希找回；同名诱饵被拒绝 |

## 目录结构

```
proxyworkstation/
  core/
    identity.py       # SHA-256 内容身份
    framemap.py       # 帧映射与不支持变换的显式拒绝
    ffutils.py        # ffmpeg/ffprobe 封装（代理生成、精确抽帧、成片渲染）
    verification.py   # 代理验证（全帧解码 + 抽帧 MAE + 往返）
    store.py          # SQLite 项目库（sources / proxies / clips）
    workstation.py    # 应用服务层（UI 与 CLI 的唯一入口）
    errors.py         # 领域异常
  ui/
    main_window.py    # 主窗口：素材库、清晰度切换、时间线、代理任务、导出
    preview.py        # 按帧号解码的帧预览（显示代理帧 -> 源帧号）
    relocation.py     # 路径重定位对话框（哈希匹配，拒绝同名异内容）
    export_dialog.py  # 导出参数与渲染前检查对话框
    workers.py        # 后台线程，代理任务不冻结界面
scripts/
  make_samples.py     # 生成带可见帧号、两种原分辨率的样片及同名诱饵
  demo_workflow.py    # 端到端演示（含损坏/离线/搬迁全部场景）
tests/                # 45 项 pytest，含 offscreen Qt 测试
```

## 环境准备

需要 ffmpeg/ffprobe、PySide6、Pillow、pytest。本机无 root 时：

```bash
# ffmpeg 静态构建与 arm64 GL 运行库已置于 tools/，加载环境：
source tools/env.sh
pip install -r requirements.txt   # 或使用已安装的用户站点包
```

## 运行

```bash
# 端到端演示（样例生成 -> 代理 -> 剪辑 -> 离线 -> 重定位 -> 成片）
python3 -I scripts/demo_workflow.py /tmp/pws_demo

# 测试套件
source tools/env.sh
python3 -m pytest tests/ -q

# Qt 工作站
source tools/env.sh
python3 -m proxyworkstation.app ./project
```

## 验证口径

* 代理抽帧同时解码同帧号的原帧与代理帧，归一化到代理分辨率后计算 MAE，
  阈值默认 12（健康代理 < 1），并断言 `source -> proxy -> source` 帧号一致。
* 成片渲染用 `trim=start_frame/end_frame + setpts` 保证每段帧数精确，
  渲染后比对实际帧数与区间帧数之和，并可抽帧目视核对烧录帧号。
* 代理损坏通过 `-xerror` 全帧解码闸门捕获，截断/字节翻转均在验证阶段报错。
