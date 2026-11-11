# BrightT SP 图层检查插件

**v1.3.2 · 制作者 xiangbian · 适用于 Substance 3D Painter 10.0 及以上**

交付工程前，扫描所有 Texture Set 和 Stack，找出名称含汉字、自身关闭或受父级隐藏影响的图层、文件夹和效果。从结果行可跳回 Painter 的对应位置，批量命名，或导出完整检查报告。

## 安装

1. 下载 [插件脚本](./BrightT_SP_Layer_Checker.py)，在 GitHub 文件页选择 **Download raw file**。只需安装这一个 Python 文件。
2. 关闭 Painter，把脚本直接放进插件目录。Windows 使用实际「文档」下的 `Adobe\Adobe Substance 3D Painter\python\plugins\`，macOS 使用 `~/Documents/Adobe/Adobe Substance 3D Painter/python/plugins/`。若安装过旧名 `sp_layer_checker.py`，先将旧文件移出该目录。
3. 重启 Painter，在 **Python → BrightT_SP_Layer_Checker** 勾选启用。关闭面板后，可从 **Window → BrightT_SP_Layer_Checker** 重新打开。

## 检查工程

1. 打开 `.spp`，等工程载入后点击 **开始检查**。侧栏可以按问题类别筛选，也可以搜索图层名或材质集。
2. 点击结果行定位到图层或效果。保持 **Layers** 面板可见。若已选中但没滚动，点击 **复制定位诊断** 获取信息。
3. **中文命名** 检测名称里的汉字。选中待处理行，点 **自定义命名**，可统一前后缀、添加序号，或双击预览逐项修改。例如前缀 `BT_`、名称 `Metal_Rust`、三位序号可生成 `BT_Metal_Rust_001`。核对后点 **应用命名**。整批操作可一次撤销。
4. **关闭眼睛** 表示节点自己关眼，点击该行的眼睛按钮可直接切换。**父级隐藏** 表示上级文件夹或所属图层关眼，子层即使开眼也可能不可见。审核时可保留刻意隐藏的备用层。
5. 处理完再次点 **开始检查**。用 **复制报告** 或 **导出 CSV** 保存完整问题清单及读取错误，最后手动保存工程。筛选只影响当前列表。

## 检查范围与验证

插件检查图层内容效果和蒙版效果。实例共享效果仅在源图层位置检查一次。透明度、通道、蒙版自身的启用状态及最终渲染效果需在 Painter 中另行检查。出现 **检查不完整** 时，按错误路径排查后重新扫描。

v1.3.2 的 67 项离线单元测试均已通过。测试使用模拟 Painter 接口，SP 10.0.1 的实机表现及 11.0.1 原生图层列表滚动适配仍待真实工程验证。
