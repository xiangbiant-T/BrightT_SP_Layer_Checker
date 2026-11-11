# -*- coding: utf-8 -*-
"""BrightT_SP_Layer_Checker 1.3.2. Install in Painter's python/plugins.

Layer audit with explicit rename and locate actions. No Painter/Qt core imports,
so they can also be tested outside Painter using API fixtures.
"""
import csv
import importlib
import io
import re
import time
from datetime import datetime

VERSION = "1.3.2"
APP_NAME = "BrightT_SP_Layer_Checker"
AUTHOR = "xiangbian"
_widgets = []
_connections = []
_panel = None
_dock = None
_api = None

# CJK unified / compatibility ideographs, including supplementary extensions.
# These are Han characters, shared by Chinese and some other writing systems.
_HAN_RANGES = (
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF), (0x2A700, 0x2EE5F), (0x2F800, 0x2FA1F),
    (0x30000, 0x3347F),
)


def contains_han(name):
    return any(ch == "〇" or any(a <= ord(ch) <= b for a, b in _HAN_RANGES)
               for ch in name)


def scan_project(api, include_inherited=True):
    """Scan all texture sets/stacks, without changing selection or project data.

    Read errors are explicit. A partial scan can never report a clean pass.
    Includes both content and mask effect stacks on source layers and groups.
    Instances are checked as nodes; their contents are audited at the source.
    Mask enable/disable toggles are separate from effect eye visibility.
    """
    if not api.project.is_open():
        raise RuntimeError("请先打开一个 SP 工程，等待加载完成后再检查。")
    if getattr(api, "layerstack", None) is None:
        raise RuntimeError("当前 SP 缺少 layerstack 接口。本插件需要 SP 10.0 或更高版本。")
    if not callable(getattr(api.layerstack, "get_root_layer_nodes", None)):
        raise RuntimeError("当前 SP 缺少读取图层的接口，无法完成检查。")

    report = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "所有 Texture Set / 所有 Stack / 图层与文件夹 / 图层效果 / 蒙版效果",
        "include_inherited": include_inherited,
        "scanned": 0, "texture_sets": 0, "stacks": 0,
        "layers": 0, "content_effects": 0, "mask_effects": 0,
        "han": 0, "hidden": 0, "inherited": 0,
        "rows": [], "errors": [], "all_nodes": [], "project_path": None,
        "instance_links": [], "painter_version": "未知",
    }
    try:
        report["painter_version"] = ".".join(str(part) for part in api.application.version_info())
    except (AttributeError, TypeError):
        pass
    visited_layers = {}
    instance_edges = {}
    try:
        report["project_path"] = api.project.file_path()
    except Exception:
        pass  # Unsaved projects are guarded by the panel's project lifecycle.

    def error(where, exc):
        report["errors"].append("{}：{}: {}".format(where, type(exc).__name__, exc))

    try:
        texture_sets = list(api.textureset.all_texture_sets())
    except Exception as exc:
        error("读取 Texture Set 列表失败", exc)
        return report
    if not texture_sets:
        report["errors"].append("没有读取到 Texture Set；请确认工程已加载完成。")
    for ts_index, texture_set in enumerate(texture_sets, 1):
        report["texture_sets"] += 1
        try:
            ts_name = texture_set.name()
        except Exception as exc:
            ts_name = "Texture Set #{}（名称读取失败）".format(ts_index)
            error(ts_name, exc)
        try:
            stacks = list(texture_set.all_stacks())
        except Exception as exc:
            error(ts_name + " / 读取 Stack 列表失败", exc)
            continue
        if not stacks:
            report["errors"].append(ts_name + " / 没有读取到 Stack。")
        for stack_index, stack in enumerate(stacks, 1):
            report["stacks"] += 1
            try:
                stack_name = stack.name() or "默认 Stack"
            except Exception as exc:
                stack_name = "Stack #{}（名称读取失败）".format(stack_index)
                error(ts_name + " / " + stack_name, exc)
            location = ts_name + " / " + stack_name
            try:
                roots = list(api.layerstack.get_root_layer_nodes(stack))
            except Exception as exc:
                error(location + " / 读取根图层失败", exc)
                continue

            # Iterative depth-first traversal avoids Python's recursion limit.
            # Ancestry is retained as strings only; no live node refs are exported.
            pending = [(node, (), (), frozenset(), "layers", None) for node in reversed(roots)]
            while pending:
                node, parents, hidden_parents, ancestry, category, parent_uid = pending.pop()
                try:
                    node_uid = node.uid()
                except Exception as exc:
                    error(location + " / " + " > ".join(parents) + " / 读取节点标识失败", exc)
                    continue
                if node_uid in ancestry:
                    report["errors"].append(location + " / 检测到异常循环层级。")
                    continue
                report["scanned"] += 1
                report[category] += 1
                name_read_ok = True
                try:
                    name = node.get_name()
                except Exception as exc:
                    name_read_ok = False
                    name = "[名称读取失败 #{}]".format(report["scanned"])
                    error(location + " / " + " > ".join(parents + (name,)), exc)
                path_parts = parents + (name,)
                path = " > ".join(path_parts)
                try:
                    node_type = node.get_type().name
                except Exception as exc:
                    node_type = "未知类型"
                    error(location + " / " + path + " / 读取类型失败", exc)
                if category == "layers":
                    visited_layers[(ts_name, stack_name, node_uid)] = (node_type, path)
                try:
                    visible = node.is_visible()
                    if not isinstance(visible, bool):
                        raise TypeError("is_visible() 未返回 bool")
                except Exception as exc:
                    visible = None
                    error(location + " / " + path + " / 读取可见性失败", exc)

                han = name_read_ok and contains_han(name)
                hidden = visible is False
                inherited = bool(hidden_parents)
                report["han"] += int(han)
                report["hidden"] += int(hidden)
                report["inherited"] += int(inherited)
                issues = []
                if not name_read_ok:
                    issues.append("名称读取失败")
                if han:
                    issues.append("名称包含中文（汉字）")
                if hidden:
                    issues.append("自身可见性关闭")
                if include_inherited and inherited:
                    issues.append("受父级隐藏影响")
                if visible is None:
                    issues.append("可见性读取失败")
                record = {
                        "texture_set": ts_name, "stack": stack_name,
                        "path": path, "type": node_type,
                        "issues": "；".join(issues),
                        "eye": {True: "开启", False: "关闭", None: "未知"}[visible],
                        "hidden_parent": "；".join(hidden_parents),
                        "category": category, "han": han,
                        "hidden": hidden, "inherited": inherited,
                        "uid": node_uid, "name": name, "name_read_ok": name_read_ok,
                        "parent_uid": parent_uid,
                    }
                report["all_nodes"].append(record)
                if issues:
                    report["rows"].append(record)

                # Effects are leaves. Their eye state does not propagate to
                # sibling effects, and their lists are not queried as layers.
                if category != "layers":
                    continue

                # Instance targets have no independently editable content stack.
                # Calling content_effects() on them can fail in Painter's native
                # list binding. Query the documented source instead, and verify
                # after traversal that its content was covered in the full scan.
                if node_type == "InstanceLayer":
                    try:
                        source = node.instance_source()
                        source_key = (source.get_texture_set().name(),
                                      source.get_stack().name() or "默认 Stack", source.uid())
                        instance_key = (ts_name, stack_name, node_uid)
                        instance_edges[instance_key] = source_key
                        report["instance_links"].append({
                            "texture_set": ts_name, "stack": stack_name, "path": path,
                            "source_texture_set": source_key[0], "source_stack": source_key[1],
                            "source_name": source.get_name(), "source_path": "待验证",
                            "_instance_key": instance_key, "_source_key": source_key,
                        })
                    except Exception as exc:
                        error(location + " / " + path + " / 读取实例源失败（InstanceLayer）", exc)
                    continue

                child_hidden = hidden_parents + ((path,) if hidden else ())
                child_ancestry = ancestry | {node_uid}
                descendants = []
                # Read each branch independently: a failed child-layer branch
                # must not suppress the group's content or mask effect stacks.
                if node_type == "GroupLayer" or callable(getattr(node, "sub_layers", None)):
                    try:
                        children = list(node.sub_layers())
                    except Exception as exc:
                        error(location + " / " + path + " / 读取子图层失败", exc)
                    else:
                        descendants.extend((child, path_parts, child_hidden, child_ancestry, "layers", node_uid)
                                           for child in children)
                for method_name, label in (("content_effects", "[图层效果]"),
                                           ("mask_effects", "[蒙版效果]")):
                    try:
                        effects = list(getattr(node, method_name)())
                    except Exception as exc:
                        error(location + " / " + path + " / 读取" + label +
                              "失败（类型：" + node_type + "）", exc)
                        continue
                    # Hidden owner layers affect both stacks. We still read every
                    # effect's own visibility, even when its owner is hidden.
                    effect_parents = path_parts + (label,)
                    descendants.extend((effect, effect_parents, child_hidden, child_ancestry, method_name, node_uid)
                                       for effect in effects)
                pending.extend(reversed(descendants))

    for link in report["instance_links"]:
        # Resolve chains, and reject a cycle or a source outside the visited tree.
        # Never turn an unverified source into an empty, successfully checked list.
        seen = {link["_instance_key"]}
        source_key = link["_source_key"]
        while source_key in instance_edges and source_key not in seen:
            seen.add(source_key)
            source_key = instance_edges[source_key]
        where = link["texture_set"] + " / " + link["path"]
        if source_key in seen:
            link["source_path"] = "实例引用循环，未完成检查"
            report["errors"].append(where + " / 实例源引用形成循环，无法确认源效果覆盖。")
        elif source_key not in visited_layers or visited_layers[source_key][0] in ("InstanceLayer", "未知类型"):
            link["source_path"] = "源内容未确认覆盖"
            report["errors"].append(where + " / 实例源内容未确认覆盖：" +
                                    link["source_texture_set"] + " / " + link["source_name"])
        else:
            link["source_texture_set"], link["source_stack"] = source_key[:2]
            link["source_path"] = visited_layers[source_key][1]
        del link["_instance_key"]
        del link["_source_key"]
    return report


def summary(report):
    prefix = "检查不完整" if report["errors"] else "检查完成"
    return ("{}｜{} 个 Texture Set，{} 个 Stack，共检查 {} 个节点\n"
            "图层/文件夹 {} 个；图层效果 {} 个；蒙版效果 {} 个。\n"
            "中文命名 {} 个；自身关闭 {} 个；受父级隐藏影响 {} 个；结果 {} 行。\n"
            "实例关联 {} 个；实例共享内容在源图层位置检查。"
            .format(prefix, report["texture_sets"], report["stacks"], report["scanned"],
                    report["layers"], report["content_effects"], report["mask_effects"],
                    report["han"], report["hidden"], report["inherited"], len(report["rows"]),
                    len(report["instance_links"])))


HEADERS = ("Texture Set", "Stack", "图层 / 效果路径", "类型", "问题", "自身可见性", "隐藏父级路径")
KEYS = ("texture_set", "stack", "path", "type", "issues", "eye", "hidden_parent")


def _spreadsheet_text(value):
    text = str(value)
    # Keep layer names as text when opened by spreadsheet applications.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def report_text(report, delimiter=","):
    """A self-contained snapshot, including incomplete-read errors and counts."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow((APP_NAME, "版本 " + VERSION, "制作者 " + AUTHOR))
    writer.writerow(("SP 版本", report["painter_version"]))
    writer.writerow(("检查时间", report["time"]))
    writer.writerow(("检查范围", report["scope"]))
    writer.writerow(("列出受父级隐藏影响的图层和效果", "是" if report["include_inherited"] else "否"))
    writer.writerow(("摘要", summary(report)))
    writer.writerow(())
    writer.writerow(HEADERS)
    for row in report["rows"]:
        writer.writerow([_spreadsheet_text(row[key]) for key in KEYS])
    if not report["rows"]:
        writer.writerow(("（无结果行）",))
    if report["instance_links"]:
        writer.writerow(())
        writer.writerow(("实例关联：共享效果在源图层位置检查一次；读取失败仍会列为错误",))
        writer.writerow(("实例 Texture Set", "实例 Stack", "实例路径",
                         "源 Texture Set", "源 Stack", "源路径"))
        for link in report["instance_links"]:
            writer.writerow([_spreadsheet_text(link[key]) for key in
                             ("texture_set", "stack", "path", "source_texture_set", "source_stack", "source_path")])
    if report["errors"]:
        writer.writerow(())
        writer.writerow(("读取错误：此次检查不完整，不能据此判定通过",))
        for message in report["errors"]:
            writer.writerow((_spreadsheet_text(message),))
    writer.writerow(())
    writer.writerow(("说明", "结果是检查时的快照；修改后需重新检查。实例共享内容在源图层处检查，不重复计数或逐实例展开。包括图层、文件夹、图层效果及蒙版效果的名称和眼睛状态；不检查蒙版本身的启用开关、透明度或通道。"))
    return output.getvalue()


# Editable suggestions use a local asset glossary, never an online service.
ASSET_GLOSSARY = {
    "基础颜色": "BaseColor", "基本颜色": "BaseColor", "颜色调整": "Color_Adjust",
    "色阶调整": "Levels_Adjust", "粗糙度": "Roughness", "金属度": "Metallic",
    "环境遮蔽": "Ambient_Occlusion", "法线": "Normal", "高度": "Height",
    "透明度": "Opacity", "自发光": "Emissive", "颜色": "Color", "色阶": "Levels",
    "木纹": "Wood_Grain", "木材": "Wood", "木头": "Wood", "金属": "Metal",
    "铁锈": "Rust", "锈迹": "Rust", "生锈": "Rust", "铜锈": "Patina",
    "划痕": "Scratches", "刮痕": "Scratches", "磨损": "Wear", "边缘": "Edge",
    "边缘磨损": "Edge_Wear", "污垢": "Dirt", "脏污": "Dirt", "污渍": "Stains",
    "灰尘": "Dust", "泥土": "Mud", "泥巴": "Mud", "油漆": "Paint", "掉漆": "Paint_Chipping",
    "石头": "Stone", "石材": "Stone", "砖块": "Bricks", "砖墙": "Brick_Wall",
    "混凝土": "Concrete", "塑料": "Plastic", "橡胶": "Rubber", "玻璃": "Glass",
    "布料": "Fabric", "皮革": "Leather", "绳子": "Rope", "裂纹": "Cracks",
    "凹陷": "Dents", "凸起": "Bumps", "细节": "Detail", "噪波": "Noise", "噪点": "Noise",
    "填充": "Fill", "绘制": "Paint", "滤镜": "Filter", "生成器": "Generator",
    "蒙版": "Mask", "遮罩": "Mask", "模糊": "Blur", "锐化": "Sharpen", "锚点": "Anchor",
    "图层": "Layer", "文件夹": "Group", "分组": "Group", "副本": "Copy",
    "背景": "Background", "底色": "BaseColor", "底层": "Base", "基础": "Base",
    "顶部": "Top", "底部": "Bottom", "左侧": "Left", "右侧": "Right",
    "高光": "Highlight", "阴影": "Shadow", "暗部": "Dark", "亮部": "Light",
    "红色": "Red", "绿色": "Green", "蓝色": "Blue", "白色": "White", "黑色": "Black",
    "黄色": "Yellow", "棕色": "Brown", "灰色": "Gray", "测试": "Test",
    "顏色": "Color", "圖層": "Layer", "紋理": "Texture", "纹理": "Texture",
}


def parse_glossary(text):
    glossary = dict(ASSET_GLOSSARY)
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("词表第 {} 行请使用 中文=English 格式。".format(number))
        source, target = (part.strip() for part in line.split("=", 1))
        if not source or not contains_han(source) or not re.fullmatch(r"[A-Za-z0-9_ .-]+", target):
            raise ValueError("词表第 {} 行无效；左侧含汉字，右侧使用英文、数字、空格或 _.-。".format(number))
        glossary[source] = target
    return glossary


def translate_asset_name(name, glossary=None):
    glossary = ASSET_GLOSSARY if glossary is None else glossary
    pattern = re.compile("|".join(re.escape(key) for key in sorted(glossary, key=len, reverse=True)))
    translated = pattern.sub(lambda match: "_" + glossary[match.group()] + "_", name)
    if contains_han(translated):
        return None  # Unknown text must be manually named; never drop it silently.
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9.-]+", "_", translated)).strip("_") or None


def custom_names(count, base, prefix="", suffix="", numbered=True, start=1, digits=3):
    base = base.strip()
    if not base:
        raise ValueError("请输入自定义名称。")
    if start < 0 or not 1 <= digits <= 6:
        raise ValueError("序号起始值不能为负数，位数应为 1–6。")
    return [prefix.strip() + base + suffix.strip() +
            ("_" + str(start + index).zfill(digits) if numbered else "")
            for index in range(count)]


def node_identity(row):
    return row["texture_set"], row["stack"], row["uid"]


def naming_scope(row):
    return row["texture_set"], row["stack"], row["parent_uid"], row["category"]


def validate_rename_plan(plan, all_nodes):
    if not plan:
        raise ValueError("没有选中要改名的图层或效果。")
    target_ids = [node_identity(item["row"]) for item in plan]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("改名列表包含重复节点，请重新生成预览。")
    target_ids = set(target_ids)
    occupied = {(naming_scope(row), row["name"].casefold()) for row in all_nodes
                if node_identity(row) not in target_ids and row["name_read_ok"]}
    for item in plan:
        name = item["new_name"]
        if not name or name != name.strip() or not re.fullmatch(r"[A-Za-z0-9_ .-]+", name):
            raise ValueError("新名称需使用英文、数字、空格或 _.-，且不能留空：" + repr(name))
        if name in (".", "..") or len(name) > 200:
            raise ValueError("名称无效或超过 200 个字符：" + name)
        key = naming_scope(item["row"]), name.casefold()
        if key in occupied:
            raise ValueError("同一层级下名称重复：{}。请加序号或修改预览。".format(name))
        occupied.add(key)


def resolve_row(api, row, require_name=True):
    if not api.project.is_open():
        raise RuntimeError("工程已关闭，请打开工程并重新检查。")
    node = api.layerstack.get_node_by_uid(row["uid"])
    # Some API docs annotate this function as returning a list.
    if isinstance(node, (list, tuple)):
        if len(node) != 1:
            raise RuntimeError("无法唯一定位节点，请重新检查。")
        node = node[0]
    parent = node.get_parent()
    current = (node.get_texture_set().name(), node.get_stack().name() or "默认 Stack",
               node.get_type().name, parent.uid() if parent is not None else None)
    expected = (row["texture_set"], row["stack"], row["type"], row["parent_uid"])
    if current != expected or (require_name and node.get_name() != row["name"]):
        raise RuntimeError("图层名称或位置已变化，请重新检查后再操作。")
    if row["category"] != "layers":
        in_mask = node.is_in_mask_stack()
        if in_mask != (row["category"] == "mask_effects"):
            raise RuntimeError("效果所在栈已变化，请重新检查。")
    return node


class LocateRequest:
    """A single click, advanced on the UI thread between Painter refreshes.

    advance() returns the next timer interval, or None after verified success.
    Never sleeps, pumps nested events, scans the project, or uses worker threads.
    """
    TIMEOUT_SECONDS = 5.0
    MAX_SELECT_ATTEMPTS = 3

    def __init__(self, api, row, project_path=None, clock=time.monotonic):
        self.api = api
        self.row = dict(row)
        self.project_path = project_path
        self.clock = clock
        self.started = clock()
        self.phase = "start"
        self.attempts = 0
        self.prepare_attempts = 0
        self.confirmations = 0
        self.owner = None
        self.selection_type = None
        self.last_error = ""

    def active_matches(self):
        active = self.api.textureset.get_active_stack()
        return active is not None and (
            active.material().name(), active.name() or "默认 Stack"
        ) == (self.row["texture_set"], self.row["stack"])

    def target_selected(self):
        selected = self.api.layerstack.get_selected_nodes(self.stack)
        return ([node.uid() for node in selected] == [self.row["uid"]]
                and (self.owner is None or
                     self.api.layerstack.get_selection_type(self.owner) == self.selection_type))

    def advance(self):
        api = self.api
        if not api.project.is_open():
            raise RuntimeError("工程已关闭，定位已取消。")
        if self.project_path is not None and api.project.file_path() != self.project_path:
            raise RuntimeError("工程已切换，请重新检查。")
        if self.clock() - self.started > self.TIMEOUT_SECONDS:
            raise RuntimeError("SP 未在等待时间内完成定位。请等待工程加载完成后重试。" + self.last_error)
        if self.phase == "start":
            self.node = resolve_row(api, self.row)
            self.stack = self.node.get_stack()
            self.parents = []
            parent = self.node.get_parent()
            seen = {self.row["uid"]}
            while parent is not None:
                if parent.uid() in seen:
                    raise RuntimeError("节点父级出现循环，无法定位。")
                seen.add(parent.uid())
                self.parents.append(parent)
                parent = parent.get_parent()
            if self.row["category"] != "layers":
                if not self.parents:
                    raise RuntimeError("找不到效果所属图层，请重新检查。")
                self.owner = self.parents[0]
                self.selection_type = (api.layerstack.SelectionType.Mask
                                       if self.row["category"] == "mask_effects"
                                       else api.layerstack.SelectionType.Content)
            self.phase = "wait_stack"
            if not self.active_matches():
                # Issue once, then wait; repeat clicks must not restart the switch.
                api.textureset.set_active_stack(self.stack)
                return 20

        if self.phase == "wait_stack":
            if not self.active_matches():
                return 40
            self.phase = "prepare"

        if not self.active_matches():
            raise RuntimeError("当前 Texture Set / Stack 已改变，定位已停止。")

        if self.phase == "prepare":
            # Revalidate after a deferred switch, before any selection writes.
            self.node = resolve_row(api, self.row)
            changed = False
            for parent in reversed(self.parents):
                if parent.get_type().name == "GroupLayer" and parent.is_collapsed():
                    parent.set_collapsed(False)
                    changed = True
            if self.owner is not None:
                if self.prepare_attempts >= self.MAX_SELECT_ATTEMPTS:
                    raise RuntimeError("SP 尚未就绪，无法选中效果所属的内容或蒙版。" + self.last_error)
                try:
                    if api.layerstack.get_selection_type(self.owner) != self.selection_type:
                        selected = api.layerstack.get_selected_nodes(self.stack)
                        if [node.uid() for node in selected] != [self.owner.uid()]:
                            api.layerstack.set_selected_nodes([self.owner])
                        api.layerstack.set_selection_type(self.owner, self.selection_type)
                        changed = True
                except (ValueError, RuntimeError) as exc:
                    self.prepare_attempts += 1
                    self.last_error = " 原因：" + str(exc)
                    return 80
            self.phase = "select"
            if changed:
                return 20  # Let the tree/mask refresh before selecting its child.

        if self.phase == "select":
            self.node = resolve_row(api, self.row)
            if not self.target_selected():
                if self.attempts >= self.MAX_SELECT_ATTEMPTS:
                    raise RuntimeError("SP 尚未保留目标选中状态，请等待界面就绪后重试。" + self.last_error)
                self.attempts += 1
                try:
                    api.layerstack.set_selected_nodes([self.node])
                except (ValueError, RuntimeError) as exc:
                    # The active stack may be ready before its selection model.
                    self.last_error = " 原因：" + str(exc)
                    return 80
            self.phase = "verify"
            self.verify_polls = 0
            return 40

        if self.phase == "verify":
            if self.target_selected():
                self.confirmations += 1
                if self.confirmations >= 2:
                    return None
                return 20
            self.confirmations = 0
            self.verify_polls += 1
            if self.verify_polls >= 4:
                self.phase = "prepare"
            return 40


def toggle_row_visibility(api, report, row):
    """Toggle the live eye state of exactly one validated node, with readback."""
    if report.get("project_path") is not None and api.project.file_path() != report["project_path"]:
        raise RuntimeError("工程已切换，请重新检查。")
    node = resolve_row(api, row)
    old = node.is_visible()
    if not isinstance(old, bool):
        raise RuntimeError("无法读取当前眼睛状态，请重新检查。")
    value = not old
    with api.layerstack.ScopedModification(APP_NAME + " 切换眼睛"):
        try:
            node.set_visible(value)
            if node.is_visible() is not value:
                raise RuntimeError("SP 返回的眼睛状态与设置值不一致。")
        except Exception as exc:
            try:
                node.set_visible(old)
                if node.is_visible() is not old:
                    raise RuntimeError("恢复校验失败")
            except Exception as rollback_exc:
                raise RuntimeError("切换失败且未能恢复原状态，请在 SP 中撤销并重新检查：{}；{}"
                                   .format(exc, rollback_exc)) from exc
            raise RuntimeError("眼睛切换失败，已恢复原状态：" + str(exc)) from exc
    return value


def update_visibility_snapshot(report, row, visible):
    """Update this scan snapshot, including descendants, without rescanning SP.

    External edits and undo still require a new scan. all_nodes is in DFS order.
    """
    key = node_identity(row)
    parents = {}
    for item in report["all_nodes"]:
        if node_identity(item) == key:
            item["eye"] = "开启" if visible else "关闭"
            item["hidden"] = not visible
        parent_key = (item["texture_set"], item["stack"], item["parent_uid"])
        hidden_parents = parents.get(parent_key, ())
        item["hidden_parent"] = "；".join(hidden_parents)
        item["inherited"] = bool(hidden_parents)
        parents[node_identity(item)] = hidden_parents + ((item["path"],) if item["hidden"] else ())
        issues = []
        if not item["name_read_ok"]:
            issues.append("名称读取失败")
        if item["han"]:
            issues.append("名称包含中文（汉字）")
        if item["hidden"]:
            issues.append("自身可见性关闭")
        if report["include_inherited"] and item["inherited"]:
            issues.append("受父级隐藏影响")
        if item["eye"] == "未知":
            issues.append("可见性读取失败")
        item["issues"] = "；".join(issues)
    report["rows"] = [item for item in report["all_nodes"] if item["issues"]]
    for flag in ("han", "hidden", "inherited"):
        report[flag] = sum(bool(item[flag]) for item in report["all_nodes"])


class LayerPanelScroller:
    """Qt compatibility adapter for Painter's Layers panel.

    Supports SP 11.0.1's observed selected row widgets and scroll areas, plus
    standard Qt item views. Uses actual row geometry, never guessed row heights
    or a name search. Unsupported UI structures fail explicitly with diagnostics.
    """
    def __init__(self, QtCore, QtWidgets, main_window, excluded):
        if main_window is None:
            raise RuntimeError("SP 主窗口不可用")
        self.core, self.widgets = QtCore, QtWidgets
        self.main_window, self.excluded = main_window, excluded
        self.detail = ""
        self.last_view = None
        self.last_index = None
        self.target_uid = None
        self.mode = None
        self.native_row = None

    def native_roots(self):
        # Actual SP 11.0.1 class, observed in the user's diagnostic. Ignore the
        # toolbar button and dock that also use the objectName LayersStackView.
        return [widget for widget in self.main_window.findChildren(self.widgets.QWidget, "LayersStackView")
                if widget.metaObject().className().split("::")[-1] == "LayersStackView"
                and widget.isVisible() and widget is not self.excluded
                and not self.excluded.isAncestorOf(widget)]

    def native_candidates(self):
        selected = []
        for root in self.native_roots():
            # Native findChildren is not capped by the verbose diagnostic limit.
            for widget in root.findChildren(self.widgets.QWidget, "wrappedWidget_selectionZone"):
                if widget.isVisible() and widget.property("selected") is True:
                    selected.append(widget)
        # An effect can have a selected owner container as well. Use the deepest
        # selected row; independent selected rows remain ambiguous and are refused.
        return [widget for widget in selected if not any(
            widget is not other and widget.isAncestorOf(other) for other in selected)]

    def native_header(self, row_widget):
        # Expanded layer widgets may contain a tall effects list. Reveal the
        # actual layer header in that case, not the entire expanded subtree.
        for header in row_widget.findChildren(self.widgets.QWidget, "layerWidget"):
            parent = header.parentWidget()
            while parent is not None and parent is not row_widget:
                if parent.objectName() == "wrappedWidget_selectionZone":
                    break
                parent = parent.parentWidget()
            if parent is row_widget and header.isVisible() and header.height() > 0:
                return header
        return row_widget

    def native_scroll_chain(self, widget):
        areas = []
        parent = widget.parentWidget()
        while parent is not None:
            if (isinstance(parent, self.widgets.QAbstractScrollArea)
                    and parent.viewport().isAncestorOf(widget)):
                areas.append(parent)
            if parent.metaObject().className().split("::")[-1] == "LayersStackView":
                break
            parent = parent.parentWidget()
        return areas

    def reveal_native(self, selected):
        widget = selected[0]
        header = self.native_header(widget)
        areas = self.native_scroll_chain(header)
        if not areas or any(area.viewport().height() <= 0 for area in areas):
            self.detail = "选中行的滚动区域尚未展开或尺寸尚未就绪"
            return False
        for area in areas:  # Inner effect list first, then the outer layer list.
            viewport = area.viewport()
            position = header.mapTo(viewport, self.core.QPoint(0, 0))
            bar = area.verticalScrollBar()
            delta = position.y() + (min(header.height(), viewport.height()) - viewport.height()) / 2
            destination = max(bar.minimum(), min(bar.maximum(), round(bar.value() + delta)))
            if bar.value() != destination:
                bar.setValue(destination)
        self.mode = "native"
        self.native_row = widget
        self.detail = "已请求 SP 原生 Layers 滚动，等待实际位置刷新"
        return True

    def native_is_visible(self):
        selected = self.native_candidates()
        if len(selected) != 1 or selected[0] is not self.native_row:
            return False
        header = self.native_header(selected[0])
        areas = self.native_scroll_chain(header)
        if not areas or not header.isVisible() or header.height() <= 0:
            return False
        for area in areas:
            viewport = area.viewport()
            y = header.mapTo(viewport, self.core.QPoint(0, 0)).y()
            if viewport.height() <= 0 or y < 0 or y + header.height() > viewport.height():
                return False
        return True

    def is_layers_view(self, view):
        current = view
        identified = False
        while current is not None:
            if current is self.excluded or isinstance(current, self.widgets.QComboBox):
                return False
            name = current.objectName().casefold()
            cls = current.metaObject().className().casefold()
            compact = re.sub(r"[^a-z]", "", name + " " + cls)
            title = current.windowTitle().strip().casefold() if isinstance(current, self.widgets.QWidget) else ""
            if any(token in compact for token in ("layerstack", "layersstack")) or title in ("layers", "图层", "圖層", "layer stack", "图层堆栈"):
                identified = True
            current = current.parent()
        return identified and not isinstance(view, self.widgets.QHeaderView)

    def candidates(self, row):
        candidates = []
        for view in self.main_window.findChildren(self.widgets.QAbstractItemView):
            if not view.isVisible() or not self.is_layers_view(view):
                continue
            selection = view.selectionModel()
            if selection is None:
                continue
            indexes = []
            for index in selection.selectedIndexes():
                first = index.sibling(index.row(), 0)
                if first.isValid() and first not in indexes:
                    indexes.append(first)
            if len(indexes) != 1:
                continue
            index = indexes[0]
            # Check named UID roles when the host exposes them. A mismatch means
            # the view is still stale, even if its selected label is identical.
            uid_values = []
            for role, name in view.model().roleNames().items():
                normalized = re.sub(r"[^a-z]", "", bytes(name).decode("utf-8", "replace").casefold())
                if normalized in ("uid", "nodeuid", "layeruid", "nodeid", "layerid"):
                    value = index.data(role)
                    if isinstance(value, (int, str)) and not isinstance(value, bool):
                        uid_values.append(str(value))
            if uid_values and str(row["uid"]) not in uid_values:
                continue
            candidates.append((view, index, bool(uid_values)))
        exact = [item for item in candidates if item[2]]
        return exact or candidates

    def reveal(self, row):
        self.target_uid = row["uid"]
        if self.native_roots():
            selected = self.native_candidates()
            if len(selected) == 1:
                return self.reveal_native(selected)
            self.detail = "SP 原生图层行尚未同步选中状态" if not selected else "SP 原生图层面板存在多个选中行"
            return False
        candidates = self.candidates(row)
        if len(candidates) != 1:
            self.detail = ("未找到唯一可控制的 Layers 图层列表" if not candidates
                           else "找到多个图层列表，无法确定滚动对象")
            return False
        view, index, _ = candidates[0]
        if isinstance(view, self.widgets.QTreeView):
            parents = []
            parent = index.parent()
            while parent.isValid():
                parents.append(parent)
                parent = parent.parent()
            for parent in reversed(parents):
                if not view.isExpanded(parent):
                    view.expand(parent)
        horizontal = view.horizontalScrollBar().value()
        view.scrollTo(index, self.widgets.QAbstractItemView.PositionAtCenter)
        view.horizontalScrollBar().setValue(horizontal)
        self.last_view = view
        self.last_index = self.core.QPersistentModelIndex(index)
        self.mode = "itemview"
        self.detail = "已请求滚动，等待界面刷新"
        return True

    def is_visible(self, row):
        if self.mode == "native":
            return self.native_is_visible()
        candidates = self.candidates(row)
        if len(candidates) != 1 or self.last_index is None:
            return False
        view, index, _ = candidates[0]
        if view is not self.last_view or index != self.last_index:
            return False
        rect = view.visualRect(index)
        viewport = view.viewport().rect()
        # Test vertical visibility: a wide name column may extend horizontally.
        return (view.isVisible() and rect.isValid() and rect.height() > 0
                and rect.top() >= viewport.top() and rect.bottom() <= viewport.bottom())

    def diagnostics(self):
        """Read the actual Layers subtree, including non-ItemView/QML controls.

        No meta-method invocation, setters, scrolling or project edits. Report
        property schemas and numeric/bool state, not text field contents.
        """
        lines = ["Layers 滚动诊断 v2", "结果：" + self.detail,
                 "target_uid={}".format(self.target_uid)]
        if self.main_window is None:
            return "\n".join(lines + ["无法访问 SP 主窗口"])
        qt_package = self.core.__name__.split(".")[0]
        quick, quick_widgets = None, None
        for module in ("QtQuick", "QtQuickWidgets"):
            try:
                loaded = importlib.import_module(qt_package + "." + module)
                if module == "QtQuick": quick = loaded
                else: quick_widgets = loaded
            except ImportError:
                lines.append(module + " unavailable")

        def label(obj):
            return obj.metaObject().className() + ":" + obj.objectName().replace("\n", " ")[:160]

        # Put the selected native rows before the verbose tree so that large
        # projects cannot truncate the information needed to diagnose scrolling.
        native_selected = self.native_candidates()
        lines.append("native_selected_rows={}".format(len(native_selected)))
        for widget in native_selected[:12]:
            header = self.native_header(widget)
            lines.append("native_selected=" + label(widget) + " header=" + label(header))
            for area in self.native_scroll_chain(header):
                y = header.mapTo(area.viewport(), self.core.QPoint(0, 0)).y()
                bar = area.verticalScrollBar()
                lines.append("  native_scroll={} value={} max={} target_y={} target_h={} viewport_h={}".format(
                    label(area), bar.value(), bar.maximum(), y, header.height(), area.viewport().height()))

        roots = []
        docks = []
        for widget in self.main_window.findChildren(self.widgets.QWidget):
            if widget is self.excluded or self.excluded.isAncestorOf(widget):
                continue
            cls = widget.metaObject().className().split("::")[-1].casefold()
            name = widget.objectName().casefold()
            if (cls in ("layersstackview", "layerstackview") or name in ("layersstackview", "layerstackview")) and not isinstance(widget, self.widgets.QAbstractButton):
                if isinstance(widget, self.widgets.QDockWidget): docks.append(widget)
                else: roots.append(widget)
            elif isinstance(widget, self.widgets.QDockWidget) and widget.windowTitle().strip().casefold() in ("layers", "图层", "圖層"):
                docks.append(widget)
        if not roots:
            roots = docks
        roots = [obj for obj in roots if not any(other is not obj and other.isAncestorOf(obj) for other in roots)]
        lines.append("layers_roots={}".format(len(roots)))
        if not roots:
            lines.append("Layers 根控件未找到。可见停靠面板标识：")
            lines.extend(label(dock) for dock in self.main_window.findChildren(self.widgets.QDockWidget)
                         if dock.isVisible() and not dock.isAncestorOf(self.excluded))
            return "\n".join(lines)

        schema_seen, seen = set(), set()
        queue = [(root, 0, 0) for root in reversed(roots)]
        count = 0
        relevant = re.compile(r"scroll|select|current|layer|node|uid|model|index|content|count|item|clip|offset", re.I)
        scalar_types = {"bool", "int", "uint", "long", "ulong", "qlonglong", "qulonglong", "double", "float", "qreal"}
        while queue and count < 2500:
            obj, depth, parent_number = queue.pop()
            if obj in seen or obj is self.excluded:
                continue
            seen.add(obj)
            count += 1
            number = count
            prefix = "#{} parent=#{} ".format(number, parent_number)
            try:
                meta = obj.metaObject()
                cls = meta.className()
                parts = [prefix + label(obj)]
                if isinstance(obj, self.widgets.QWidget):
                    g = obj.geometry()
                    parts.append("visible={} rect=({},{},{},{})".format(obj.isVisible(), g.x(), g.y(), g.width(), g.height()))
                elif quick is not None and isinstance(obj, quick.QQuickItem):
                    parts.append("quick visible={} xywh=({},{},{},{})".format(
                        obj.isVisible(), obj.x(), obj.y(), obj.width(), obj.height()))
                lines.append(" | ".join(parts))

                properties = [meta.property(i) for i in range(meta.propertyCount())]
                values = []
                for prop in properties:
                    name = prop.name()
                    if relevant.search(name) and prop.typeName() in scalar_types and prop.isReadable():
                        try:
                            value = obj.property(name)
                            if isinstance(value, (bool, int, float)):
                                values.append("{}={}".format(name, value))
                        except (RuntimeError, TypeError):
                            values.append(name + "=<unavailable>")
                for name_bytes in obj.dynamicPropertyNames():
                    name = bytes(name_bytes).decode("utf-8", "replace")
                    if relevant.search(name):
                        try:
                            value = obj.property(name)
                            if isinstance(value, (bool, int, float)):
                                values.append("dynamic:{}={}".format(name, value))
                            else:
                                values.append("dynamic:{}=<{}>".format(name, type(value).__name__))
                        except (RuntimeError, TypeError):
                            values.append("dynamic:" + name + "=<unavailable>")
                if values: lines.append("  state " + ", ".join(values))

                if cls not in schema_seen:
                    schema_seen.add(cls)
                    declarations = ["{}:{}".format(prop.name(), prop.typeName()) for prop in properties if relevant.search(prop.name())]
                    methods = []
                    for i in range(meta.methodCount()):
                        method = meta.method(i)
                        signature = bytes(method.methodSignature()).decode("utf-8", "replace")
                        if relevant.search(signature) and method.methodType() != self.core.QMetaMethod.Signal:
                            methods.append(signature)
                    if declarations: lines.append("  property_schema " + ", ".join(declarations))
                    if methods: lines.append("  method_schema [read only; not invoked] " + "; ".join(methods))

                if isinstance(obj, self.widgets.QAbstractScrollArea):
                    bar, viewport = obj.verticalScrollBar(), obj.viewport()
                    lines.append("  scroll value={} min={} max={} page={} viewport={}x{}".format(
                        bar.value(), bar.minimum(), bar.maximum(), bar.pageStep(), viewport.width(), viewport.height()))
                if isinstance(obj, self.widgets.QAbstractItemView):
                    model, selection = obj.model(), obj.selectionModel()
                    if model is not None:
                        roles = ",".join("{}:{}".format(role, bytes(name).decode("utf-8", "replace")) for role, name in model.roleNames().items())
                        lines.append("  model={} rows={} columns={} roles={}".format(
                            label(model), model.rowCount(), model.columnCount(), roles))
                    if selection is not None:
                        lines.append("  selected_indexes=" + str([(idx.row(), idx.column()) for idx in selection.selectedIndexes()][:24]))
                if isinstance(obj, self.widgets.QGraphicsView) and obj.scene() is not None:
                    scene = obj.scene()
                    selected = scene.selectedItems()
                    items = selected + [item for item in scene.items() if item not in selected][:120]
                    for item in items:
                        rect = item.sceneBoundingRect()
                        lines.append("  graphics type={} selected={} visible={} rect=({},{},{},{})".format(
                            type(item).__name__, item.isSelected(), item.isVisible(), rect.x(), rect.y(), rect.width(), rect.height()))
                children = list(obj.children())
                if quick_widgets is not None and isinstance(obj, quick_widgets.QQuickWidget):
                    root = obj.rootObject()
                    lines.append("  quick_root=" + (label(root) if root is not None else "None"))
                    if root is not None: children.append(root)
                if quick is not None and isinstance(obj, quick.QQuickWindow):
                    children.append(obj.contentItem())
                if quick is not None and isinstance(obj, quick.QQuickItem):
                    children.extend(obj.childItems())
                if depth < 40:
                    queue.extend((child, depth + 1, number) for child in reversed(children) if child not in seen)
                elif children:
                    lines.append("  <depth limit>")
            except (RuntimeError, TypeError, AttributeError) as exc:
                lines.append(prefix + "<inspection error: {}>".format(type(exc).__name__))
        lines.append("inspected={} truncated={}".format(count, bool(queue)))
        return "\n".join(lines)


def apply_rename_plan(api, report, plan):
    """Preflight all targets, write as one undo command, then verify each value.

    A setter failure triggers best-effort rollback and a clear error, never a
    silent partial success. No project save, deletion or visibility changes.
    """
    if report.get("project_path") is not None and api.project.file_path() != report["project_path"]:
        raise RuntimeError("当前工程与检查时不同，请重新检查。")
    fresh = scan_project(api, report["include_inherited"])
    if fresh["errors"]:
        raise RuntimeError("重新核对时发现读取错误，暂不能批量改名。请先完成一次无读取错误的检查。")
    validate_rename_plan(plan, fresh["all_nodes"])
    resolved = [(resolve_row(api, item["row"]), item["row"]["name"], item["new_name"]) for item in plan]
    changed = [(node, old, new) for node, old, new in resolved if old != new]
    if not changed:
        return 0
    with api.layerstack.ScopedModification(APP_NAME + " 批量命名"):
        attempted = []
        try:
            for node, old, new in changed:
                attempted.append((node, old))
                node.set_name(new)
                if node.get_name() != new:
                    raise RuntimeError("SP 返回的新名称与预览不一致。")
            for node, old, new in changed:
                if node.get_name() != new:
                    raise RuntimeError("关联图层的名称发生联动，最终结果与预览不一致。")
        except Exception as exc:
            rollback_errors = []
            for node, old in reversed(attempted):
                try:
                    node.set_name(old)
                    if node.get_name() != old:
                        raise RuntimeError("恢复校验失败")
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            for node, old in attempted:
                try:
                    if node.get_name() != old:
                        rollback_errors.append("原名称未恢复：" + old)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            if rollback_errors:
                raise RuntimeError("改名失败，部分名称未能恢复。请在 SP 中撤销并重新检查。原因：{}；恢复错误：{}"
                                   .format(exc, "；".join(rollback_errors))) from exc
            raise RuntimeError("改名失败，已恢复本批次原名称：" + str(exc)) from exc
    return len(changed)


def _create_panel(api):
    if api.application.version_info() < (10, 1, 0):
        from PySide2 import QtCore, QtGui, QtWidgets
    else:
        from PySide6 import QtCore, QtGui, QtWidgets

    theme = """
    QWidget#SP_Layer_Checker_v1 {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #30385f,stop:1 #202640);
    }
    QWidget {
        color:#edf0ff; font-family:'Microsoft YaHei UI','Noto Sans CJK SC','Segoe UI'; font-size:12px;
    }
    QLabel { background:transparent; border:0; }
    QLabel#Eyebrow { color:#a0aaca; font-size:10px; letter-spacing:2px; }
    QLabel#Heading { font-size:24px; font-weight:600; }
    QLabel#Muted { color:#a5aecf; font-size:11px; }
    QLabel#SectionTitle { font-size:14px; font-weight:600; }
    QLabel#StatusBadge { color:#bcb5ff; background:#36365e; border:1px solid #53517c;
                         padding:5px 12px; border-radius:11px; }
    QFrame#Sidebar { background:#23294b; border-right:1px solid #3b4265; }
    QScrollArea#MainScroll, QWidget#MainContent { background:transparent; border:0; }
    QScrollArea#SideScroll { background:#23294b; border:0; }
    QLabel#Brand { color:#f3f1ff; font-size:20px; font-weight:700; }
    QLabel#BrandMark { background:#7770df; color:white; font-size:21px; border-radius:10px; }
    QPushButton { background:#363e65; border:1px solid #49527a; padding:8px 14px; border-radius:7px; }
    QPushButton:hover { background:#444d79; border-color:#727db0; }
    QPushButton:pressed { background:#4c5684; }
    QPushButton:disabled { color:#7c86a9; background:#2c3355; border-color:#3b4364; }
    QPushButton#Primary { background:#8174ee; border:1px solid #9489fa; color:white; font-weight:600; }
    QPushButton#Primary:hover { background:#9386ff; }
    QPushButton#Primary:disabled { background:#4b467c; color:#9b96bb; border-color:#5e578d; }
    QToolButton#EyeToggle { background:#343c60; border:1px solid #566183; border-radius:5px; padding:2px 5px; }
    QToolButton#EyeToggle:hover { background:#49567f; border-color:#9b91ef; }
    QPushButton#Nav { text-align:left; border:1px solid transparent; background:transparent;
                     color:#a7b0d2; padding:11px 12px; border-radius:7px; }
    QPushButton#Nav:hover { background:#303858; color:#f2f1ff; }
    QPushButton#Nav:checked { color:#e9e4ff; background:#423e70; border-color:#625b99; }
    QFrame#Metric { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3a426c,stop:1 #30375e);
                     border:1px solid #4a527d; border-radius:10px; }
    QLabel#MetricValue { font-size:31px; font-weight:600; }
    QLabel#MetricLabel { color:#b7bfdc; font-size:11px; }
    QFrame#Coverage, QFrame#ResultCard { background:#272e50; border:1px solid #41496d; border-radius:10px; }
    QLineEdit { background:#222947; border:1px solid #444e74; border-radius:7px;
                padding:8px 12px; selection-background-color:#776bd2; }
    QLineEdit:focus { border:1px solid #9183f4; }
    QSpinBox { background:#30385b; color:#edf0ff; border:1px solid #50597f;
               border-radius:5px; padding:6px 10px; min-width:55px; }
    QComboBox { background:#30385b; border:1px solid #50597f; border-radius:7px; padding:7px 12px; }
    QComboBox QAbstractItemView { background:#2b3256; selection-background-color:#665ba3; }
    QCheckBox { color:#b6bfdb; spacing:8px; }
    QCheckBox::indicator { width:14px; height:14px; border:1px solid #68729b; border-radius:4px; background:#262e50; }
    QCheckBox::indicator:checked { background:#8b7df0; border:2px solid #b5aaff; }
    QTableWidget { background:#272e50; alternate-background-color:#2c3457; border:0;
                   gridline-color:#3a4266; selection-background-color:#494775; selection-color:#ffffff; }
    QTableWidget::item { padding:9px 8px; border-bottom:1px solid #363e61; }
    QTableWidget::item:selected { background:#494775; color:white; }
    QHeaderView::section { background:#30395d; color:#aeb9d8; border:0; border-bottom:1px solid #485278;
                          padding:10px 8px; text-align:left; font-size:11px; }
    QTableCornerButton::section { background:#30395d; border:0; }
    QScrollBar:vertical { background:#242b49; width:10px; margin:0; }
    QScrollBar::handle:vertical { background:#505a85; border-radius:5px; min-height:25px; }
    QScrollBar:horizontal { background:#242b49; height:10px; margin:0; }
    QScrollBar::handle:horizontal { background:#505a85; border-radius:5px; min-width:25px; }
    QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
    QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
    QProgressBar { background:#3a4263; border:0; border-radius:3px; min-height:5px; max-height:5px; }
    QProgressBar::chunk { background:#8b7cf3; border-radius:3px; }
    QPlainTextEdit { background:#3a2d48; color:#ffb5ad; border:1px solid #735065; border-radius:7px; padding:8px; }
    QToolTip { color:#f5f3ff; background:#333b61; border:1px solid #7d76ac; padding:7px; }
    """

    class RenameDialog(QtWidgets.QDialog):
        def __init__(self, parent, rows, all_nodes):
            super().__init__(parent)
            self.rows = rows
            self.all_nodes = all_nodes
            self.plan = []
            self.setObjectName("SP_Layer_Checker_v1")
            self.setStyleSheet(theme)
            self.setWindowTitle(APP_NAME + " · 自定义命名")
            self.resize(780, 660)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(22, 20, 22, 20)
            layout.setSpacing(12)
            title = QtWidgets.QLabel("自定义命名")
            title.setObjectName("Heading")
            layout.addWidget(title)
            intro = QtWidgets.QLabel("已选 {} 项 · 双击下方“新名称”可逐项修改 · 应用后可在 SP 中撤销".format(len(rows)))
            intro.setWordWrap(True)
            intro.setObjectName("Muted")
            layout.addWidget(intro)
            self.mode = QtWidgets.QComboBox()
            self.mode.addItems(("自定义名称", "离线术语转英文（可编辑）"))
            layout.addWidget(self.mode)
            form = QtWidgets.QFormLayout()
            self.base = QtWidgets.QLineEdit()
            self.base.setPlaceholderText("例如 Metal_Rust；也可以直接逐行填写下方新名称")
            form.addRow("自定义名称", self.base)
            parts = QtWidgets.QHBoxLayout()
            self.prefix = QtWidgets.QLineEdit()
            self.prefix.setPlaceholderText("前缀，例如 BT_")
            self.suffix = QtWidgets.QLineEdit()
            self.suffix.setPlaceholderText("后缀，例如 _Detail")
            parts.addWidget(self.prefix)
            parts.addWidget(self.suffix)
            form.addRow("前缀 / 后缀", parts)
            numbering = QtWidgets.QHBoxLayout()
            self.numbered = QtWidgets.QCheckBox("添加序号")
            self.numbered.setChecked(len(rows) > 1)
            self.start = QtWidgets.QSpinBox()
            self.start.setRange(0, 999999)
            self.start.setValue(1)
            self.digits = QtWidgets.QSpinBox()
            self.digits.setRange(1, 6)
            self.digits.setValue(3)
            numbering.addWidget(self.numbered)
            numbering.addWidget(QtWidgets.QLabel("起始"))
            numbering.addWidget(self.start)
            numbering.addWidget(QtWidgets.QLabel("位数"))
            numbering.addWidget(self.digits)
            form.addRow("序号规则", numbering)
            layout.addLayout(form)
            self.glossary = QtWidgets.QPlainTextEdit()
            self.glossary.setMaximumHeight(80)
            self.glossary.setPlaceholderText("可补充术语，每行一组，例如：\n船体=Hull\n门框=Door_Frame")
            self.glossary.setVisible(False)
            layout.addWidget(self.glossary)
            note = QtWidgets.QLabel("修改上方规则会重新生成预览。仅使用英文、数字、空格或 _.-；同一层级不允许重名。")
            note.setWordWrap(True)
            note.setObjectName("Muted")
            layout.addWidget(note)
            self.preview = QtWidgets.QTableWidget(len(rows), 3)
            self.preview.setHorizontalHeaderLabels(("所属位置", "原名称", "新名称（可编辑）"))
            self.preview.setAlternatingRowColors(True)
            self.preview.verticalHeader().setVisible(False)
            self.preview.horizontalHeader().setStretchLastSection(True)
            self.preview.setColumnWidth(0, 230)
            self.preview.setColumnWidth(1, 170)
            self.preview.setMinimumHeight(200)
            self.preview.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
            layout.addWidget(self.preview, 1)
            self.feedback = QtWidgets.QLabel()
            self.feedback.setWordWrap(True)
            self.feedback.setTextFormat(QtCore.Qt.PlainText)
            layout.addWidget(self.feedback)
            actions = QtWidgets.QHBoxLayout()
            actions.addStretch()
            cancel = QtWidgets.QPushButton("取消")
            cancel.clicked.connect(self.reject)
            self.apply_button = QtWidgets.QPushButton("应用命名")
            self.apply_button.setObjectName("Primary")
            self.apply_button.clicked.connect(self.accept)
            actions.addWidget(cancel)
            actions.addWidget(self.apply_button)
            layout.addLayout(actions)
            self.preview.itemChanged.connect(self.validate_preview)
            self.mode.currentIndexChanged.connect(self.mode_changed)
            for edit in (self.base, self.prefix, self.suffix):
                edit.textChanged.connect(self.generate_preview)
            self.glossary.textChanged.connect(self.generate_preview)
            self.numbered.toggled.connect(self.generate_preview)
            self.start.valueChanged.connect(self.generate_preview)
            self.digits.valueChanged.connect(self.generate_preview)
            self.generate_preview()

        def mode_changed(self, *_args):
            translating = self.mode.currentIndex() == 1
            self.base.setEnabled(not translating)
            self.glossary.setVisible(translating)
            self.generate_preview()

        def generate_preview(self, *_args):
            error = ""
            try:
                if self.mode.currentIndex() == 0:
                    names = custom_names(len(self.rows), self.base.text(), self.prefix.text(),
                                         self.suffix.text(), self.numbered.isChecked(), self.start.value(), self.digits.value())
                else:
                    glossary = parse_glossary(self.glossary.toPlainText())
                    names = []
                    for index, row in enumerate(self.rows):
                        translated = translate_asset_name(row["name"], glossary)
                        name = translated if translated is not None else row["name"]
                        name = self.prefix.text().strip() + name + self.suffix.text().strip()
                        if self.numbered.isChecked():
                            name += "_" + str(self.start.value() + index).zfill(self.digits.value())
                        names.append(name)
            except ValueError as exc:
                names = [""] * len(self.rows)
                error = str(exc)
            self.preview.blockSignals(True)
            for index, row in enumerate(self.rows):
                for column, value in enumerate((row["texture_set"] + " / " + row["path"], row["name"], names[index])):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column != 2:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    self.preview.setItem(index, column, item)
            self.preview.blockSignals(False)
            self.validate_preview()
            if error:
                self.feedback.setText(error)
                self.apply_button.setEnabled(False)

        def validate_preview(self, *_args):
            self.plan = [{"row": row, "new_name": self.preview.item(index, 2).text()}
                         for index, row in enumerate(self.rows) if self.preview.item(index, 2) is not None]
            try:
                validate_rename_plan(self.plan, self.all_nodes)
                changed = sum(item["new_name"] != item["row"]["name"] for item in self.plan)
                self.feedback.setStyleSheet("color:#a6c6f5;")
                self.feedback.setText("将修改 {} / {} 项；点击应用后生效。".format(changed, len(self.plan)))
                self.apply_button.setEnabled(changed > 0)
            except ValueError as exc:
                self.feedback.setStyleSheet("color:#ffa6a6;")
                self.feedback.setText(str(exc) + (" 未识别的中文请手动填写英文，或补充术语。" if self.mode.currentIndex() else ""))
                self.apply_button.setEnabled(False)


    class CheckerPanel(QtWidgets.QWidget):
        FILTERS = (("all", "全部结果"), ("han", "中文命名"), ("hidden", "关闭眼睛"),
                   ("inherited", "父级隐藏"), ("content_effects", "图层效果"), ("mask_effects", "蒙版效果"))

        def label(self, text, role=None):
            widget = QtWidgets.QLabel(text)
            widget.setTextFormat(QtCore.Qt.PlainText)
            if role:
                widget.setObjectName(role)
            return widget

        def __init__(self):
            super().__init__()
            self.report = None
            self.active_filter = "all"
            self._wide = None
            self._layout_mode = None
            self._locate_request = None
            self._scroller = None
            self._scroll_attempts = 0
            self._scroll_waiting = False
            self._scroll_error = ""
            self._hidden_session_ids = set()
            self._eye_busy = False
            self._locate_timer = QtCore.QTimer(self)
            self._locate_timer.setSingleShot(True)
            self._locate_timer.timeout.connect(self.advance_location)
            self.setObjectName("SP_Layer_Checker_v1")
            self.setWindowTitle(APP_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self.setStyleSheet(theme)
            self.setMinimumSize(520, 440)
            self.resize(1160, 800)
            root = QtWidgets.QHBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            self.sidebar = QtWidgets.QFrame()
            self.sidebar.setObjectName("Sidebar")
            self.sidebar.setMinimumWidth(0)
            side = QtWidgets.QVBoxLayout(self.sidebar)
            self.side_layout = side
            side.setContentsMargins(16, 28, 16, 20)
            side.setSpacing(8)
            brand_row = QtWidgets.QHBoxLayout()
            mark = self.label("◈", "BrandMark")
            self.brand_mark = mark
            mark.setFixedSize(36, 36)
            mark.setAlignment(QtCore.Qt.AlignCenter)
            brand_row.addWidget(mark)
            self.brand_label = self.label("BrightT", "Brand")
            brand_row.addWidget(self.brand_label)
            side.addLayout(brand_row)
            side.addSpacing(34)
            side.addWidget(self.label("检查结果", "Eyebrow"))
            self.nav = {}
            for index, (key, label) in enumerate(self.FILTERS):
                if index == 4:
                    side.addSpacing(22)
                    side.addWidget(self.label("按效果位置", "Eyebrow"))
                button = QtWidgets.QPushButton(label)
                button.setObjectName("Nav")
                button.setCheckable(True)
                button.clicked.connect(lambda _checked=False, key=key: self.set_filter(key))
                self.nav[key] = button
                side.addWidget(button)
            side.addStretch()
            side.addWidget(self.label("SUBSTANCE 3D\nPAINTER", "Eyebrow"))
            side.addWidget(self.label("制作者 " + AUTHOR, "Muted"))
            side.addWidget(self.label("SP Layer Checker\nv" + VERSION, "Muted"))
            self.side_scroll = QtWidgets.QScrollArea()
            self.side_scroll.setObjectName("SideScroll")
            self.side_scroll.setWidgetResizable(True)
            self.side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.side_scroll.setFixedWidth(174)
            self.side_scroll.setWidget(self.sidebar)
            root.addWidget(self.side_scroll)

            content = QtWidgets.QWidget()
            content.setObjectName("MainContent")
            body = QtWidgets.QVBoxLayout(content)
            self.body_layout = body
            body.setContentsMargins(24, 24, 24, 18)
            body.setSpacing(16)
            top = QtWidgets.QHBoxLayout()
            self.top_layout = top
            headings = QtWidgets.QVBoxLayout()
            headings.setSpacing(5)
            headings.addWidget(self.label("ASSET QUALITY  /  OVERVIEW", "Eyebrow"))
            app_title = self.label(APP_NAME, "Heading")
            app_title.setStyleSheet("font-size:18px;font-weight:600;")
            app_title.setWordWrap(True)
            headings.addWidget(app_title)
            headings.addWidget(self.label("制作者 " + AUTHOR, "Muted"))
            top.addLayout(headings)
            top.addStretch()
            self.badge = self.label("待检查", "StatusBadge")
            top.addWidget(self.badge, 0, QtCore.Qt.AlignTop)
            body.addLayout(top)
            subtitle = self.label("从图层到每一个效果，检查命名与可见性。", "Muted")
            body.addWidget(subtitle)

            self.metrics_layout = QtWidgets.QGridLayout()
            self.metrics_layout.setSpacing(12)
            self.metric_frames, self.metric_values = [], {}
            metric_specs = (("scanned", "已检查节点", "全部图层与效果", "#e8ecff"),
                            ("han", "中文命名", "名称含汉字", "#b2a1ff"),
                            ("hidden", "关闭眼睛", "节点自身关闭", "#ff9b9d"),
                            ("inherited", "父级隐藏", "受隐藏父级影响", "#88b4ff"))
            for key, title, caption, color in metric_specs:
                card = QtWidgets.QFrame()
                card.setObjectName("Metric")
                card_layout = QtWidgets.QVBoxLayout(card)
                card_layout.setContentsMargins(16, 12, 16, 12)
                card_layout.setSpacing(4)
                card_layout.addWidget(self.label(title, "MetricLabel"))
                value = self.label("—", "MetricValue")
                value.setStyleSheet("color:" + color + ";")
                card_layout.addWidget(value)
                card_layout.addWidget(self.label(caption, "Muted"))
                self.metric_frames.append(card)
                self.metric_values[key] = value
            body.addLayout(self.metrics_layout)

            coverage = QtWidgets.QFrame()
            coverage.setObjectName("Coverage")
            coverage_layout = QtWidgets.QVBoxLayout(coverage)
            coverage_layout.setContentsMargins(16, 12, 16, 12)
            coverage_title = QtWidgets.QHBoxLayout()
            coverage_title.addWidget(self.label("扫描覆盖", "SectionTitle"))
            coverage_title.addStretch()
            self.coverage_meta = self.label("所有 Texture Set · 所有 Stack", "Muted")
            coverage_title.addWidget(self.coverage_meta)
            coverage_layout.addLayout(coverage_title)
            branches = QtWidgets.QHBoxLayout()
            branches.setSpacing(20)
            self.branch_counts, self.branch_bars = {}, {}
            for key, title, color in (("layers", "图层 / 文件夹", "#8ba8fa"),
                                      ("content_effects", "图层效果", "#a392f6"),
                                      ("mask_effects", "蒙版效果", "#f399b3")):
                branch = QtWidgets.QVBoxLayout()
                branch_title = QtWidgets.QHBoxLayout()
                branch_title.addWidget(self.label(title, "Muted"))
                count = self.label("—")
                count.setStyleSheet("color:" + color + ";font-weight:600;")
                branch_title.addWidget(count, 0, QtCore.Qt.AlignRight)
                branch.addLayout(branch_title)
                bar = QtWidgets.QProgressBar()
                bar.setTextVisible(False)
                bar.setRange(0, 1000)
                bar.setValue(0)
                bar.setStyleSheet("QProgressBar::chunk {background:" + color + ";}")
                branch.addWidget(bar)
                branches.addLayout(branch, 1)
                self.branch_counts[key], self.branch_bars[key] = count, bar
            coverage_layout.addLayout(branches)
            body.addWidget(coverage)

            actions = QtWidgets.QHBoxLayout()
            actions.setSpacing(10)
            self.scan_button = QtWidgets.QPushButton("开始检查")
            self.scan_button.setObjectName("Primary")
            self.scan_button.setMinimumWidth(120)
            self.scan_button.clicked.connect(self.scan)
            self.copy_button = QtWidgets.QPushButton("复制报告")
            self.copy_button.clicked.connect(self.copy_report)
            self.export_button = QtWidgets.QPushButton("导出 CSV")
            self.export_button.clicked.connect(self.export_report)
            actions.addWidget(self.scan_button)
            actions.addStretch()
            actions.addWidget(self.copy_button)
            actions.addWidget(self.export_button)
            body.addLayout(actions)
            self.inherited = QtWidgets.QCheckBox("同时列出受父级隐藏影响的图层和效果")
            self.inherited.setChecked(True)
            self.inherited.toggled.connect(self.invalidate)
            body.addWidget(self.inherited)

            result_card = QtWidgets.QFrame()
            result_card.setObjectName("ResultCard")
            results = QtWidgets.QVBoxLayout(result_card)
            results.setContentsMargins(14, 14, 14, 12)
            results.setSpacing(10)
            result_head = QtWidgets.QHBoxLayout()
            self.result_heading = self.label("检查结果", "SectionTitle")
            result_head.addWidget(self.result_heading)
            result_head.addStretch()
            self.result_count = self.label("等待扫描", "Muted")
            result_head.addWidget(self.result_count)
            results.addLayout(result_head)
            search_row = QtWidgets.QHBoxLayout()
            self.compact_filter = QtWidgets.QComboBox()
            for key, title in self.FILTERS:
                self.compact_filter.addItem(title, key)
            self.compact_filter.currentIndexChanged.connect(self.combo_filter)
            search_row.addWidget(self.compact_filter)
            self.search = QtWidgets.QLineEdit()
            self.search.setPlaceholderText("搜索图层、效果或 Texture Set…")
            self.search.textChanged.connect(self.render_rows)
            search_row.addWidget(self.search, 1)
            results.addLayout(search_row)
            edit_row = QtWidgets.QGridLayout()
            self.edit_layout = edit_row
            self.rename_button = QtWidgets.QPushButton("自定义命名")
            self.rename_button.setObjectName("Primary")
            self.rename_button.clicked.connect(self.open_rename)
            self.locate_button = QtWidgets.QPushButton("定位选中项")
            self.locate_button.clicked.connect(self.locate_selected)
            self.auto_locate = QtWidgets.QCheckBox("点击问题自动定位")
            self.auto_locate.setChecked(True)
            self.auto_locate.toggled.connect(self.cancel_location)
            edit_row.addWidget(self.rename_button, 0, 0)
            edit_row.addWidget(self.locate_button, 0, 1)
            edit_row.setColumnStretch(2, 1)
            edit_row.addWidget(self.auto_locate, 0, 3)
            results.addLayout(edit_row)
            self.table = QtWidgets.QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(("图层 / 效果路径", "问题", "自身眼睛", "Texture Set", "节点类型"))
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.table.setAlternatingRowColors(True)
            self.table.setShowGrid(False)
            self.table.setWordWrap(False)
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(44)
            self.table.setMinimumHeight(110)
            self.table.horizontalHeader().setStretchLastSection(True)
            for index, width in enumerate((290, 180, 82, 130, 120)):
                self.table.setColumnWidth(index, width)
            self.table.itemSelectionChanged.connect(self.show_selected)
            self.table.cellClicked.connect(self.locate_clicked)
            results.addWidget(self.table, 1)
            self.empty = self.label("打开工程，点击“开始检查”查看结果。", "Muted")
            self.empty.setAlignment(QtCore.Qt.AlignCenter)
            self.empty.setWordWrap(True)
            results.addWidget(self.empty)
            self.detail = self.label("Ctrl / Shift 可多选问题，然后点击“自定义命名”。", "Muted")
            self.detail.setWordWrap(True)
            self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            results.addWidget(self.detail)
            body.addWidget(result_card, 1)

            self.errors = QtWidgets.QPlainTextEdit()
            self.errors.setReadOnly(True)
            self.errors.setMaximumHeight(92)
            self.errors.hide()
            body.addWidget(self.errors)
            self.status = self.label("", "Muted")
            self.status.setWordWrap(True)
            body.addWidget(self.status)
            self.scroll_diagnostic = QtWidgets.QPushButton("复制定位诊断")
            self.scroll_diagnostic.clicked.connect(self.copy_location_diagnostics)
            self.scroll_diagnostic.hide()
            body.addWidget(self.scroll_diagnostic, 0, QtCore.Qt.AlignLeft)
            self.main_scroll = QtWidgets.QScrollArea()
            self.main_scroll.setObjectName("MainScroll")
            self.main_scroll.setWidgetResizable(True)
            self.main_scroll.setWidget(content)
            root.addWidget(self.main_scroll, 1)
            self.set_filter("all")
            self.invalidate()
            self.adapt_layout()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, "metrics_layout"):
                self.adapt_layout()

        def adapt_layout(self):
            mode = 2 if self.width() >= 1040 else (1 if self.width() >= 720 else 0)
            if self._layout_mode == mode:
                return
            self._layout_mode = mode
            wide = mode == 2
            self._wide = wide
            self.side_scroll.show()
            self.side_scroll.setFixedWidth((124, 148, 174)[mode])
            margin = 16 if wide else 8
            self.side_layout.setContentsMargins(margin, 24 if wide else 16, margin, 16)
            self.brand_mark.setFixedSize(36 if wide else 24, 36 if wide else 28)
            self.brand_label.setStyleSheet("font-size:{}px;font-weight:700;".format(20 if wide else 16))
            for button in self.nav.values():
                button.setStyleSheet("" if wide else "padding:10px 5px;font-size:11px;")
            self.compact_filter.hide()
            padding = (12, 16, 24)[mode]
            self.body_layout.setContentsMargins(padding, 24, padding, 18)
            self.top_layout.setDirection(QtWidgets.QBoxLayout.TopToBottom if mode == 0 else QtWidgets.QBoxLayout.LeftToRight)
            self.edit_layout.removeWidget(self.auto_locate)
            if mode == 0:
                self.edit_layout.addWidget(self.auto_locate, 1, 0, 1, 4)
            else:
                self.edit_layout.addWidget(self.auto_locate, 0, 3)
            columns = 4 if wide else 2
            for card in self.metric_frames:
                self.metrics_layout.removeWidget(card)
            for index, card in enumerate(self.metric_frames):
                self.metrics_layout.addWidget(card, index // columns, index % columns)
            for index in range(4):
                self.metrics_layout.setColumnStretch(index, 1 if index < columns else 0)

        def combo_filter(self, index):
            self.set_filter(self.compact_filter.itemData(index))

        def set_filter(self, key):
            self.active_filter = key
            for filter_key, button in self.nav.items():
                button.setChecked(key == filter_key)
            self.compact_filter.blockSignals(True)
            self.compact_filter.setCurrentIndex(self.compact_filter.findData(key))
            self.compact_filter.blockSignals(False)
            self.render_rows()

        def matches_filter(self, row, key):
            if key == "all":
                return True
            if key in ("content_effects", "mask_effects"):
                return row["category"] == key
            return bool(row.get(key, False))

        def render_rows(self, *_args):
            if not hasattr(self, "table"):
                return
            query = self.search.text().strip().casefold()
            header = self.table.horizontalHeader()
            header.moveSection(header.visualIndex(2), 0 if self.active_filter == "hidden" else 2)
            report_rows = self.report["rows"] if self.report else []
            source_rows = (self.report["all_nodes"] if self.report and self.active_filter == "hidden" else report_rows)
            rows = [row for row in source_rows if (self.matches_filter(row, self.active_filter)
                    or (self.active_filter == "hidden" and node_identity(row) in self._hidden_session_ids))
                    and (not query or query in " ".join(str(row[k]) for k in KEYS).casefold())]
            self.table.setSortingEnabled(False)
            self.table.clearSelection()
            self.table.setRowCount(0)  # Delete old cell buttons before changing filters.
            self.table.setRowCount(len(rows))
            for row_number, row in enumerate(rows):
                issue_label = row["issues"] or ("本轮已打开" if self.active_filter == "hidden" else "")
                values = (row["path"], issue_label, row["eye"], row["texture_set"], row["type"])
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setData(QtCore.Qt.UserRole, row)
                    item.setToolTip("{} / {}\n{}\n{}".format(row["texture_set"], row["stack"], row["path"], row["issues"]))
                    if column == 1:
                        item.setForeground(QtGui.QColor("#ffaaa8" if row["hidden"] else "#bbaaff"))
                    if column == 2:
                        item.setForeground(QtGui.QColor("#ff9b9d" if row["hidden"] else "#97bde8"))
                    self.table.setItem(row_number, column, item)
                    if column == 2 and self.active_filter == "hidden":
                        button = QtWidgets.QToolButton()
                        button.setObjectName("EyeToggle")
                        button.setText(row["eye"])
                        button.setIcon(self.eye_icon(row["eye"] == "开启"))
                        button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                        button.setCursor(QtCore.Qt.PointingHandCursor)
                        button.setToolTip("点击关闭此节点" if row["eye"] == "开启" else "点击开启此节点；不改变父级眼睛")
                        button.setEnabled(row["eye"] != "未知" and row["name_read_ok"])
                        button.clicked.connect(lambda _checked=False, target=row: self.toggle_eye(target))
                        self.table.setCellWidget(row_number, column, button)
            self.table.setSortingEnabled(True)
            self.result_count.setText("显示 {} / {} 项".format(len(rows), len(report_rows)) if self.report else "等待扫描")
            if self.report and self.active_filter == "hidden":
                self.result_count.setText("显示 {} 项 · 当前关闭 {} 项".format(len(rows), self.report["hidden"]))
            title = dict(self.FILTERS).get(self.active_filter, "全部结果")
            self.result_heading.setText(title)
            for key, label in self.FILTERS:
                count = sum(1 for row in report_rows if self.matches_filter(row, key))
                self.nav[key].setText(label + ("   " + str(count) if self.report else ""))
            self.empty.setVisible(not rows)
            if self.report:
                if self.report["errors"]:
                    self.empty.setText("没有匹配结果；本次存在读取错误，请查看下方详情。")
                else:
                    self.empty.setText("当前筛选没有匹配项。" if report_rows else "本次检查未发现命名或眼睛状态问题。")
            else:
                self.empty.setText("打开工程，点击“开始检查”查看结果。")
            self.detail.setText("Ctrl / Shift 可多选问题，然后点击“自定义命名”。")
            if self.active_filter == "hidden":
                self.detail.setText("点击眼睛可开关图层 / 效果。本轮保留已打开项，重新检查后刷新列表。")
            self.rename_button.setEnabled(False)
            self.locate_button.setEnabled(False)

        def eye_icon(self, visible):
            pixmap = QtGui.QPixmap(20, 20)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor("#a9dbbf" if visible else "#ffaaa8"), 1.5))
            path = QtGui.QPainterPath(QtCore.QPointF(1, 10))
            path.cubicTo(6, 2, 14, 2, 19, 10)
            path.cubicTo(14, 18, 6, 18, 1, 10)
            painter.drawPath(path)
            painter.drawEllipse(QtCore.QPointF(10, 10), 2.5, 2.5)
            if not visible:
                painter.drawLine(3, 18, 17, 2)
            painter.end()
            return QtGui.QIcon(pixmap)

        def toggle_eye(self, row):
            if self._eye_busy or self.report is None:
                return
            self.cancel_location()
            self._eye_busy = True
            try:
                visible = toggle_row_visibility(api, self.report, row)
                update_visibility_snapshot(self.report, row, visible)
                for key, value in self.metric_values.items():
                    value.setText(str(self.report[key]))
                self.badge.setText("检查不完整" if self.report["errors"] else ("有待检查项" if self.report["rows"] else "未发现问题"))
                self.render_rows()
                for index in range(self.table.rowCount()):
                    if node_identity(self.table.item(index, 0).data(QtCore.Qt.UserRole)) == node_identity(row):
                        self.table.setCurrentCell(index, 0)
                        self.table.selectRow(index)
                        break
                current = next(item for item in self.report["all_nodes"] if node_identity(item) == node_identity(row))
                inherited = " 仍受父级隐藏影响；父级眼睛未改变。" if visible and current["inherited"] else ""
                self.status.setText("已{}：{}。{} 可在 SP 中撤销；外部修改或撤销后请重新检查。".format(
                    "开启" if visible else "关闭", row["path"], inherited))
            except Exception as exc:
                self.scan()  # Failure may have changed native state; never show a guessed eye.
                self.status.setText("眼睛切换未完成：{}".format(exc))
            finally:
                self._eye_busy = False

        def show_selected(self):
            items = self.table.selectedItems()
            self.rename_button.setEnabled(bool(items) and self.report is not None and not self.report["errors"])
            self.locate_button.setEnabled(bool(items) and self.report is not None)
            if not items:
                return
            current = self.table.currentItem()
            row = (current if current is not None and current.isSelected() else items[0]).data(QtCore.Qt.UserRole)
            if not row:
                return
            self.detail.setText("{} / {}\n{}\n{}{}".format(
                row["texture_set"], row["stack"], row["path"], row["issues"],
                " · 隐藏父级：" + row["hidden_parent"] if row["hidden_parent"] else ""))

        def selected_rows(self):
            return [self.table.item(index.row(), 0).data(QtCore.Qt.UserRole)
                    for index in sorted(self.table.selectionModel().selectedRows(), key=lambda index: index.row())]

        def locate_clicked(self, row_number, _column):
            if _column == 2 and self.active_filter == "hidden":
                return
            if self.auto_locate.isChecked():
                self.locate_item(self.table.item(row_number, 0).data(QtCore.Qt.UserRole))

        def locate_selected(self, *_args):
            item = self.table.currentItem()
            if item is not None and item.isSelected():
                self.locate_item(item.data(QtCore.Qt.UserRole))

        def locate_item(self, row):
            if not self.report or not row:
                return
            if self._locate_request is not None and node_identity(self._locate_request.row) == node_identity(row):
                return  # A repeated click must not restart an in-flight switch.
            self.cancel_location()
            self._locate_request = LocateRequest(api, row, self.report.get("project_path"))
            self.status.setText("正在定位：{} / {}。无需重复点击。".format(row["texture_set"], row["path"]))
            # Return from the table's click signal before altering Painter's UI.
            self._locate_timer.start(0)

        def advance_location(self):
            request = self._locate_request
            if request is None:
                return
            selection_ready = False
            try:
                delay = request.advance()
                # Native UI calls can dispatch events; a new request takes priority.
                if self._locate_request is not request:
                    return
                if delay is not None:
                    self._locate_timer.start(delay)
                    return
                selection_ready = True
                if self._scroller is None and not self._scroll_error:
                    try:
                        self._scroller = LayerPanelScroller(QtCore, QtWidgets, api.ui.get_main_window(), self)
                    except Exception as exc:
                        self._scroll_error = "无法访问 Layers 面板：" + str(exc)
                revealed = False
                if self._scroller is not None:
                    revealed = self._scroll_waiting and self._scroller.is_visible(request.row)
                    if not revealed and self._scroll_attempts < 5:
                        self._scroll_waiting = self._scroller.reveal(request.row)
                        self._scroll_attempts += 1
                        self.status.setText("已选中，正在滚动到 Layers 中的目标位置…")
                        self._locate_timer.start(60)
                        return
                    if not revealed:
                        self._scroll_error = self._scroller.detail
                self._locate_request = None
                row = request.row
                elapsed = round((request.clock() - request.started) * 1000)
                self.scroll_diagnostic.setVisible(not revealed)
                if revealed:
                    self.status.setText("已选中并滚动到可见区域：{} / {}（{} ms）。"
                                        .format(row["texture_set"], row["path"], elapsed))
                else:
                    self.status.setText("已选中，但自动滚动未完成：{}。请点击“复制定位诊断”反馈。".format(self._scroll_error))
            except Exception as exc:
                if self._locate_request is request:
                    self.cancel_location()
                    if selection_ready:
                        self._scroll_error = str(exc)
                        self.scroll_diagnostic.show()
                        self.status.setText("已选中，但自动滚动未完成：{}。可复制定位诊断反馈。".format(exc))
                    else:
                        self.status.setText("定位未完成：{}".format(exc))

        def cancel_location(self, *_args):
            self._locate_timer.stop()
            self._locate_request = None
            self._scroll_attempts = 0
            self._scroll_waiting = False
            self._scroll_error = ""

        def copy_location_diagnostics(self):
            if self._scroller is None:
                try:
                    self._scroller = LayerPanelScroller(QtCore, QtWidgets, api.ui.get_main_window(), self)
                except Exception as exc:
                    self._scroll_error = "无法读取图层控件：" + str(exc)
            detail = self._scroller.diagnostics() if self._scroller is not None else self._scroll_error
            selected = []
            try:
                for node in api.layerstack.get_selected_nodes(api.textureset.get_active_stack()):
                    selected.append((node.uid(), node.get_type().name, node.is_in_mask_stack()))
            except Exception:
                selected = ["当前选中节点读取失败"]
            text = "{} v{}\nSP {}\nselected(uid,type,mask)={}\n{}".format(
                APP_NAME, VERSION, api.application.version_info(), selected, detail)
            QtWidgets.QApplication.clipboard().setText(text)
            self.status.setText("Layers 内部控件诊断已复制，请粘贴反馈。此操作未改变图层。")

        def closeEvent(self, event):
            self.cancel_location()
            super().closeEvent(event)

        def hideEvent(self, event):
            self.cancel_location()
            super().hideEvent(event)

        def make_rename_dialog(self):
            rows = self.selected_rows()
            if not self.report or not rows:
                raise ValueError("请先在结果表格中选择要修改的项目。")
            if self.report["errors"]:
                raise ValueError("此次检查不完整，请先解决读取错误再改名。")
            return RenameDialog(self, rows, self.report["all_nodes"])

        def apply_dialog_plan(self, dialog, snapshot):
            if self.report is not snapshot:
                raise RuntimeError("工程或检查结果已变化，请重新检查并生成命名预览。")
            try:
                count = apply_rename_plan(api, snapshot, dialog.plan)
            finally:
                self.scan()
            self.status.setText("已修改 {} 项名称，可在 SP 中撤销本次批量命名。检查结果已刷新。".format(count))

        def open_rename(self, *_args):
            self.cancel_location()
            dialog = None
            try:
                snapshot = self.report
                dialog = self.make_rename_dialog()
                result = dialog.exec_() if api.application.version_info() < (10, 1, 0) else dialog.exec()
                if result == QtWidgets.QDialog.Accepted:
                    self.apply_dialog_plan(dialog, snapshot)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "命名未完成", str(exc))
                self.status.setText("命名未完成：{}".format(exc))
            finally:
                if dialog is not None:
                    dialog.deleteLater()

        def invalidate(self, *_args):
            self.cancel_location()
            self._hidden_session_ids.clear()
            self._scroller = None
            self.scroll_diagnostic.hide()
            self.report = None
            for value in self.metric_values.values():
                value.setText("—")
            for key in self.branch_counts:
                self.branch_counts[key].setText("—")
                self.branch_bars[key].setValue(0)
            self.coverage_meta.setText("所有 Texture Set · 所有 Stack")
            self.errors.clear()
            self.errors.hide()
            self.copy_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.scan_button.setEnabled(api.layerstack is not None)
            self.badge.setText("待检查")
            self.status.setText("结果是扫描时的快照，修改后请重新检查。仅检查名称与眼睛状态。")
            if api.layerstack is None:
                self.badge.setText("接口不可用")
                self.status.setText("需要 SP 10.0 或更高版本；当前缺少图层接口。")
            self.render_rows()

        def scan(self, *_args):
            self.invalidate()
            self.scan_button.setEnabled(False)
            self.badge.setText("扫描中")
            self.status.setText("正在检查全部图层、图层效果和蒙版效果…")
            self.repaint()
            try:
                self.report = scan_project(api, self.inherited.isChecked())
                self._hidden_session_ids = {node_identity(row) for row in self.report["all_nodes"] if row["hidden"]}
                for key, value in self.metric_values.items():
                    value.setText(str(self.report[key]))
                total = max(1, self.report["scanned"])
                for key in self.branch_counts:
                    self.branch_counts[key].setText(str(self.report[key]))
                    self.branch_bars[key].setValue(round(self.report[key] * 1000 / total))
                self.coverage_meta.setText("{} Texture Set · {} Stack".format(self.report["texture_sets"], self.report["stacks"]))
                self.badge.setText("检查不完整" if self.report["errors"] else ("有待检查项" if self.report["rows"] else "未发现问题"))
                self.render_rows()
                if self.report["errors"]:
                    self.errors.setPlainText("本次检查不完整，不能据此判定通过：\n" + "\n".join(self.report["errors"]))
                    self.errors.show()
                instance_note = ("  ·  {} 个实例关联：共享效果在源图层处检查，完整报告包含关联路径。"
                                 .format(len(self.report["instance_links"])) if self.report["instance_links"] else "")
                self.status.setText("检查时间：" + self.report["time"] + instance_note + "  ·  修改后请重新检查。")
                self.copy_button.setEnabled(True)
                self.export_button.setEnabled(True)
            except Exception as exc:
                self.report = None
                self.badge.setText("检查失败")
                self.status.setText("检查失败：{}".format(exc))
                self.empty.setText("此次检查失败，请查看下方提示后重试。")
            finally:
                self.scan_button.setEnabled(api.layerstack is not None)

        def copy_report(self, *_args):
            if self.report is not None:
                QtWidgets.QApplication.clipboard().setText(report_text(self.report, "\t"))
                self.status.setText("完整检查报告已复制；包含全部结果与读取错误。")

        def export_report(self, *_args):
            if self.report is None:
                return
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "导出完整图层检查报告", APP_NAME + "_Report.csv", "CSV (*.csv)")
            if not path:
                return
            if not path.lower().endswith(".csv"):
                path += ".csv"
                import os
                if os.path.exists(path):
                    answer = QtWidgets.QMessageBox.question(
                        self, "覆盖报告", "文件已存在，是否覆盖？\n" + path,
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.No)
                    if answer != QtWidgets.QMessageBox.Yes:
                        return
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as output:
                    output.write(report_text(self.report))
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "导出失败", str(exc))
                return
            self.status.setText("完整报告已导出：" + path)

    action_class = QtWidgets.QAction if api.application.version_info() < (10, 1, 0) else QtGui.QAction
    return CheckerPanel(), action_class


def _show_panel(*_args):
    if _dock is not None:
        _dock.show()
        _dock.raise_()


def start_plugin():
    global _api, _panel, _dock
    if _panel is not None:
        _show_panel()
        return
    from types import SimpleNamespace
    modules = {name: importlib.import_module("substance_painter." + name)
               for name in ("application", "project", "textureset", "ui", "event")}
    try:
        modules["layerstack"] = importlib.import_module("substance_painter.layerstack")
    except ImportError:
        modules["layerstack"] = None
    _api = SimpleNamespace(**modules)
    try:
        _panel, action_class = _create_panel(_api)
        _dock = _api.ui.add_dock_widget(_panel)
        _widgets.append(_dock)
        action = action_class(APP_NAME, _api.ui.get_main_window())
        action.triggered.connect(_show_panel)
        _api.ui.add_action(_api.ui.ApplicationMenu.Window, action)
        _widgets.append(action)
        for name in ("ProjectOpened", "ProjectCreated", "ProjectAboutToClose"):
            event_class = getattr(_api.event, name, None)
            if event_class is not None:
                callback = _panel.invalidate
                _api.event.DISPATCHER.connect(event_class, callback)
                _connections.append((event_class, callback))
        _show_panel()
    except Exception:
        close_plugin()
        raise


def close_plugin():
    global _api, _panel, _dock
    if _panel is not None:
        _panel.cancel_location()
    if _api is not None:
        for event_class, callback in _connections:
            try:
                _api.event.DISPATCHER.disconnect(event_class, callback)
            except Exception:
                pass
        _connections.clear()
        had_dock = _dock is not None
        for widget in reversed(_widgets):
            try:
                _api.ui.delete_ui_element(widget)
            except Exception:
                widget.deleteLater()
        if _panel is not None and not had_dock:
            _panel.deleteLater()
    _widgets.clear()
    _panel = None
    _dock = None
    _api = None


if __name__ == "__main__":
    start_plugin()
