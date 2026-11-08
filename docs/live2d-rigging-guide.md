# Chibi Live2D 绑骨指南（针对 chibi_live2d_base_v3.psd）

这份指南只讲**这个模型要做什么、按什么顺序做、每个参数怎么设**。Cubism 的具体按钮在哪、怎么拖网格，请配合视频学（见最后一节）。两者搭配最快：视频教你操作，这份告诉你在这个模型上该做哪些步骤。

目标：一个能在 VTube Studio 里眨眼、说话、转头、呼吸、头发摇晃，并能切换 6 种表情的 Q 版模型。参数名全部用 Cubism 标准名，这样项目代码（`MouthOpen`、`FaceAngleZ` 等）不用改。

预计时间（第一次做）：

| 阶段 | 时间 |
| --- | --- |
| 导入、分组、网格 | 2–4 小时 |
| 眼睛、眉毛、嘴 | 4–8 小时 |
| 头部转动 X/Y/Z | 6–12 小时（最难，最花时间） |
| 身体、呼吸 | 1–2 小时 |
| 物理 | 1–2 小时 |
| 表情开关 + 导出 + VTS 设置 | 2–3 小时 |

---

## 0. 准备

- 安装 **Live2D Cubism Editor 5**，免费版（FREE）就够这个模型用。免费版有参数数量和贴图尺寸上限，这个模型应该都在范围内；商用条款以官网为准。
- 文件：`assets/chibi-expressions-png/chibi-live2d-base-v3/chibi_live2d_base_v3.psd`，1280×1280，34 个图层。
- 左右规则：图层名里的 `-l` / `-r` 是**角色自己的**左右。`-l` 在画面右边，对应 Cubism 的 `ParamEyeLOpen` 等 “L” 参数。不要搞反。

## 1. 导入和整理部件

1. 把 PSD 拖进 Cubism，新建模型。
2. **数一下 ArtMesh 是不是 34 个。** 隐藏的图层（张嘴各部件、笑眼、嘟嘴、fx_*）也必须在。如果缺了，回到 PSD 把所有图层设为可见，再导入一次。
3. 在 Parts 面板建这些分组（部件文件夹），之后变形器也按这个结构挂：

```
Body
  topwear, handwear-r, handwear-l, legwear, neck
Head
  back hair
  face, nose
  Mouth:   mouth_closed, mouth_smile, mouth_pout, mouth_pout_small,
           mouth_open_inside, mouth_open_tongue, mouth_open_fang,
           mouth_open_line_lower, mouth_open_line_upper
  EyeR:    eyewhite-r, irides-r, eyelash-r, eye_smile-r, eyebrow-r, fx_tear-r
  EyeL:    eyewhite-l, irides-l, eyelash-l, eye_smile-l, eyebrow-l, fx_tear-l
  front hair, ahoge_dot
FX
  fx_question, fx_sweat, fx_anger
```

4. **剪切蒙版**：`irides-l` 剪切到 `eyewhite-l`，`irides-r` 剪切到 `eyewhite-r`；`mouth_open_tongue`、`mouth_open_fang` 剪切到 `mouth_open_inside`。这样眼珠和舌头不会跑出边界。
5. 隐藏图层在 Cubism 里先把不透明度设成 0（不是关掉可见性），之后用参数控制显示。

## 2. 网格

- 先对全部 ArtMesh 用**自动生成网格**（默认预设）。
- 以下几块需要更细的网格，因为它们会大幅变形：`front hair`、`back hair`（摇晃）、`eyelash-*`（眨眼要压扁）、`mouth_open_inside`（张嘴要拉伸）、`face`（转头）。用自动网格的 “密” 预设，或手动在边缘多加点。
- 小东西（`nose`、`ahoge_dot`、`fx_*`、`mouth_open_fang`）用最简单的网格就行。

## 3. 变形器结构

先搭好结构再做动作，后面会轻松很多。从外到内：

```
[旋转] Body Rotation        ← ParamBodyAngleZ
  [弯曲] Body Warp           ← ParamBodyAngleX, ParamBreath
    Body 的所有部件
    [旋转] Neck Rotation    ← ParamAngleZ（头歪）
      [弯曲] Face Warp       ← ParamAngleX, ParamAngleY
        [弯曲] Back Hair Warp   ← 物理 ParamHairBack
        face, nose
        [弯曲] Eye L / Eye R Warp
        [弯曲] Mouth Warp
        [弯曲] Front Hair Warp  ← 物理 ParamHairFront
        [弯曲] Ahoge Warp       ← 物理 ParamHairAhoge
```

关键点：`Face Warp` 下面每个部件都有自己的小 Warp，这样转头时前发、五官、后发可以移动不同的距离，形成立体感（视差）。

## 4. 参数表

先在参数面板建好全部参数，ID 必须一字不差：

| 参数 ID | 范围 | 作用 | 由谁驱动 |
| --- | --- | --- | --- |
| ParamAngleX | -30 ~ 30 | 左右转头 | VTS 面部追踪 / 程序 |
| ParamAngleY | -30 ~ 30 | 抬头低头 | 同上 |
| ParamAngleZ | -30 ~ 30 | 歪头 | 同上，思考姿势会用 |
| ParamEyeLOpen / ParamEyeROpen | 0 ~ 1（默认 1） | 睁闭眼 | VTS |
| ParamEyeBallX / ParamEyeBallY | -1 ~ 1 | 眼珠方向 | VTS，思考姿势会用 |
| ParamBrowLY / ParamBrowRY | -1 ~ 1 | 眉毛上下 | VTS / 表情 |
| ParamMouthOpenY | 0 ~ 1 | 张嘴 | **程序按语音音量驱动** |
| ParamMouthForm | -1 ~ 1 | 嘴形（可选） | VTS |
| ParamBodyAngleX / ParamBodyAngleZ | -10 ~ 10 | 身体转动、倾斜 | VTS |
| ParamBreath | 0 ~ 1 | 呼吸 | VTS 自动 |
| ParamHairFront / ParamHairBack | -1 ~ 1 | 头发摇晃 | 物理 |
| ParamHairAhoge | -1 ~ 1 | 呆毛摇晃 | 物理（自建） |
| ParamSmile | 0 ~ 1 | 笑眼 + 微笑嘴 | 表情（自建） |
| ParamPout | 0 ~ 1 | 嘟嘴 | 表情（自建） |
| ParamPoutSmall | 0 ~ 1 | 小嘟嘴 | 表情（自建） |
| ParamTear | 0 ~ 1 | 眼泪 | 表情（自建） |
| ParamSweat / ParamAnger / ParamQuestion | 0 ~ 1 | 三个特效符号 | 表情（自建） |

## 5. 逐个做动作（按这个顺序）

每做完一个参数就拖动滑块检查，再做下一个。

### 5.1 眨眼（ParamEyeLOpen / ParamEyeROpen）

三个关键帧：1、0.5、0。
- **1（睁眼）**：原样。`eye_smile-*` 不透明度 0。
- **0.5**：`eyelash-*` 往下压一半，`eyewhite-*` 垂直压扁一半（虹膜被剪切，会自动跟着变小）。
- **0（闭眼）**：`eyelash-*` 压成一条弧线并降到眼睛中线；`eyewhite-*` 压到几乎没有高度；然后 `eyelash-*`、`eyewhite-*` 不透明度设成 0，`eye_smile-*` 不透明度设成 100%。

做完一只眼，另一只照做。闭眼时用的是 ^^ 笑眼，Q 版看起来会很可爱。

### 5.2 眼珠（ParamEyeBallX / Y）

只移动 `irides-*`。X 左右各移 6–8 像素，Y 上下各移 4–5 像素。因为已经剪切到眼白，移多了也不会出界。

### 5.3 眉毛（ParamBrowLY / RY）

`eyebrow-*` 上下移动 5–8 像素。-1 时可以稍微往内倾斜，做出生气或委屈的感觉。

### 5.4 张嘴（ParamMouthOpenY）——程序最依赖的一个

两个关键帧：0、1。
- **0**：`mouth_closed` 100%；五个 `mouth_open_*` 都是 0%。
- **1**：`mouth_closed` 0%；五个 `mouth_open_*` 都是 100%；在 `Mouth Warp` 上把 `mouth_open_inside` 和 `mouth_open_line_lower` 往下拉开一点，让嘴张得更大。
- 可以再加一个 0.3 的关键帧：张嘴部件已经可见但嘴巴还很小，这样小音量时说话也看得出来。

检查：在 VTS 里接上程序后说话，嘴巴应该跟着声音开合。项目里的 `mouth_gain` / `mouth_smoothing`（`config/settings.yaml`）可以之后再调。

### 5.5 表情开关（ParamSmile、ParamPout、ParamPoutSmall、ParamTear、三个特效）

都是 0 / 1 两个关键帧：
- **ParamSmile = 1**：`eye_smile-*` 100%，睁眼的三层都是 0%；`mouth_smile` 100%，`mouth_closed` 0%。
- **ParamPout = 1**：`mouth_pout` 100%，`mouth_closed` 0%。`ParamPoutSmall` 同理，用 `mouth_pout_small`。
- **ParamTear / ParamSweat / ParamAnger / ParamQuestion = 1**：对应的 `fx_*` 100%。可以给汗滴加一点往下滑的位移。

注意：表情会和眨眼、张嘴同时作用在同一批图层上。如果出现两张嘴同时可见，就用 Cubism 的**多参数关键帧组合**（比如 ParamSmile × ParamMouthOpenY 四个角）把冲突的组合调对。第一次做可以先只保证 “笑的时候不说话” 也好看，以后再完善。

### 5.6 转头（ParamAngleX / Y / Z）——最难的一步

先只做 X，三个关键帧：-30、0、30。转向右边（+30）时：
- `Face Warp` 整体往右挤压，右边变窄，左边变宽；
- 五官（眼、嘴、鼻）往右移得**最多**；
- `front hair` 往右移得比脸**多一点**；
- `back hair` 往**左**移一点（在头后面，方向相反）；
- 耳朵附近的侧发跟着脸的边缘走。

-30 可以用 Cubism 的**左右反转**（镜像）功能从 +30 生成，再微调。然后 Y 同理（抬头时五官上移、前发上移、后发下移）。最后做 X×Y 四个角，Cubism 有**自动生成四角关键帧**的功能，生成后再修。Z 很简单：`Neck Rotation` 旋转 ±10° 左右。

这一步一定要看视频跟着做，光看文字很难掌握。

### 5.7 身体和呼吸

- **ParamBodyAngleZ**：`Body Rotation` 旋转 ±5°。
- **ParamBodyAngleX**：`Body Warp` 左右轻微挤压，两只袖子往反方向偏一点。
- **ParamBreath**：1 时 `topwear` 和两只袖子略微上移 2–3 像素，肩膀抬起一点。

## 6. 物理

在物理设置里建 3 组：

| 组 | 输入 | 输出 |
| --- | --- | --- |
| 前发 | ParamAngleX（60%）、ParamAngleZ（40%） | ParamHairFront |
| 后发 | ParamAngleX、ParamAngleZ、ParamBodyAngleX | ParamHairBack |
| 呆毛 | ParamAngleX、ParamAngleZ | ParamHairAhoge |

然后给 `Front Hair Warp`、`Back Hair Warp`、`Ahoge Warp` 在 -1 / 0 / 1 做左右摆动的形状。发梢摆得多、发根几乎不动。呆毛可以摆得夸张一点，Q 版很吃这个。

## 7. 导出并接到项目

1. 在 Cubism 里导出：**文件 → 导出运行时文件（moc3）**，贴图尺寸选 2048。会得到 `.moc3`、`.model3.json`、`.physics3.json` 和贴图文件夹。
2. 把整个文件夹复制到 VTube Studio 的 `Live2DModels` 目录，在 VTS 里加载。VTS 会自动把标准参数（AngleX、EyeLOpen、MouthOpenY……）接到面部追踪上。
3. 在 VTS 里做表情文件（.exp3.json），对应程序的情绪名：

| 程序情绪 | 建议的参数组合 |
| --- | --- |
| happy | ParamSmile 1 |
| sulking | ParamPout 1 + ParamTear 1 + ParamBrowLY/RY -0.5 |
| annoyed | ParamPoutSmall 1 + ParamAnger 1 + 眉毛 -1 |
| shocked | ParamSweat 1 + 眉毛 +1 |
| confused | ParamQuestion 1 + 一边眉毛 +0.5 |
| smug | ParamMouthForm 0.5 + 眼睛半闭（EyeOpen 0.7） |
| neutral | 不用表情 |

4. 更新 `config/settings.yaml` 的 `avatar:` 段：
   - `expressions:` 改成新的表情文件名，把 confused 和 smug 也加上（目前只配了 4 个）。
   - `actions:` 里的 `facepalm` 是旧模型的热键 ID，换模型后会失效，要在 VTS 里给新模型重建热键，再填上新的名字或 ID。
5. 运行 `.venv/Scripts/python -m app`，输入 `/avatar`。它会列出没对上的表情和动作。

## 8. 常见坑

- **参数 ID 打错**：VTS 不会报错，只是那个动作不动。检查拼写，要和第 4 节一字不差。
- **改了 PSD 怎么办**：Cubism 可以重新导入 PSD，图层名不变的话，网格和变形都会保留。所以图层名一旦定了就别改。
- **眼珠出界**：剪切蒙版没设好，回第 1 节第 4 步。
- **张嘴时还看得到闭嘴的线**：ParamMouthOpenY = 1 时忘了把 `mouth_closed` 设成 0%。
- **转头时露出脸部空白**：后发往反方向移得不够，或者前发移得太少。

## 9. 视频怎么学

建议顺序：
1. 先看 **Live2D 官方教程**（docs.live2d.com 上的 Cubism Editor 教程，有中文版），学会界面、导入、网格、变形器、参数和关键帧。
2. 再搜 “Live2D 新手 绑骨 教程” 或 “Live2D rigging tutorial beginner”，找**完整做一个模型**的系列视频，跟着做一遍眼睛和嘴。
3. 卡在转头时，单独搜 “Live2D 角度X 教程” 或 “Live2D head angle XY tutorial”。
4. 最后搜 “Live2D 物理 设置” 或 “Live2D physics hair”。

遇到具体问题可以截图来问，比如 “转头时前发穿模”。我可以对着这份 PSD 的图层帮你看哪里该改。
