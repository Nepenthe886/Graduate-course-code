"""生成无人机任务栈分层架构图（Pillow）。

输出：docs/architecture.png
可重复运行，纯绘图，不依赖外部服务。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture.png"

FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
W, H = 2400, 1800
BG = (246, 248, 252)
TEXT = (32, 38, 48)
MUTED = (90, 102, 120)
ARROW = (120, 130, 148)

LAYERS = [
    {
        "no": "L0",
        "title": "交互 / 任务下发",
        "color": (226, 236, 252),
        "label": (56, 96, 180),
        "boxes": [
            (0, 640, "用户自然语言任务\n（Port / Canyon / Flooded 等会话）"),
            (656, 640, "agent4drone GUI（main.py）\n单步指令、日志查看"),
            (1312, 640, "AutoCheck / agent_api_service.py\n下发任务 + 调用 /check/* 校验"),
        ],
    },
    {
        "no": "L1",
        "title": "Agent 运行时\n官方 agent4drone",
        "color": (232, 240, 234),
        "label": (58, 120, 84),
        "boxes": [
            (0, 640, "Agent Loop\nLLM 输出 tool_calls → 工具执行 → 结果回填"),
            (656, 640, "工具注册\n官方 35 个 + 注入导航工具 13 个"),
            (1312, 640, "Prompt / llm_rules.json\n任务分发规则、终态标记"),
        ],
    },
    {
        "no": "L2",
        "title": "LLM 接入层\n（本地模型）",
        "color": (253, 242, 226),
        "label": (188, 118, 26),
        "boxes": [
            (0, 640, "llm_adapter_proxy.py :8010\n• 目标任务强制转 nav_execute_mission\n"
                     "• 模板调用纠正 / 改写\n• 循环与失败熔断、任务互斥/取消"),
            (656, 640, "llama-server :8012\nQwen3-8B-Q4_K_M.gguf\n"
                       "本地 llama.cpp（127.0.0.1, -ngl 99）"),
            (1312, 640, "LLM 日志\nlogs/service/*/service.log\n"
                        "logs/batches/*/runs/*/llm_calls.jsonl"),
        ],
    },
    {
        "no": "L3",
        "title": "工具层\n（nav_tools.py）",
        "color": (235, 232, 250),
        "label": (92, 70, 180),
        "boxes": [
            (0, 600, "nav_execute_mission\nactions / waypoints / task_text；多机并行"),
            (616, 680, "nav_template_action\n10 个官方模板 + target_photo 目标名解析"),
            (1312, 660, "nav_plan_route / nav_map_info\nnav_route_to_target / nav_scan_area\n"
                        "nav_nearby_obstacles / nav_state …（共 13 个）"),
        ],
    },
    {
        "no": "L4",
        "title": "任务解析 / 规划\n（L0–L1）",
        "color": (226, 244, 244),
        "label": (24, 120, 124),
        "boxes": [
            (0, 520, "mission_run.build_spec_from_task\n任务文本 → 动作 spec"),
            (536, 960, "解析器：双机 route / 编队 / 分配与交换 / 共同目标 /\n"
                       "目标列表+进度 / 具名多目标 / 舰队 / 坐标点 / 模板 / waypoints\n"
                       "（地图数据分类 drone/target；支持无编号命名）"),
            (1512, 470, "template_actions.py（10 模板）\nroute_order.py（NN+2-opt）\ncoverage.py（覆盖规划）"),
        ],
    },
    {
        "no": "L5",
        "title": "执行层\n（L2 行为树）",
        "color": (250, 232, 232),
        "label": (168, 60, 60),
        "boxes": [
            (0, 520, "execute_actions / execute_mission\n入口：actions 或 waypoints"),
            (536, 900, "节点：TakeOff / NavigateToPose / MoveTo / MoveDirection /\n"
                       "ApproachDrone / TrackTarget / InspectTarget / hover /\n"
                       "take_photo / land / wait"),
            (1452, 530, "多机并行（ThreadPoolExecutor）\nper-drone 绑定 + approach barrier\n"
                        "300s deadline、取消事件、失败清理 land"),
        ],
    },
    {
        "no": "L6",
        "title": "原子动作 / 规划\n（L3–L4）",
        "color": (238, 238, 238),
        "label": (88, 92, 102),
        "boxes": [
            (0, 600, "actions.py：官方 11 命令\ntake_off / move_to / move_along_path /\n"
                     "hover / take_photo / land …"),
            (616, 640, "planner.py\n网格 A* + detour Dijkstra\n平滑、修复、free_point_near"),
            (1272, 700, "L4 路线自检 / 修复\n_route_blocker / _repair_route\n"
                        "clearance=0.3、drone_radius=0.2\nsensors.py / geometry.py / worldmap.py"),
        ],
    },
    {
        "no": "L7",
        "title": "仿真服务器\nMultiUAV v0.49",
        "color": (242, 236, 226),
        "label": (120, 92, 36),
        "boxes": [
            (0, 600, "REST API :8000\n/sessions  /drones  /targets\n/command  /check/*"),
            (616, 640, "UAVSessions/*.json\n会话地图、目标、障碍\ntask_type / 任务定义"),
            (1272, 700, "校验 API\ntarget_is_reached / photo / hover /\n"
                        "directed_distance / task_progress\n日志：server_logs、mission_run.log"),
        ],
    },
]


def _font(path: str, size: int):
    return ImageFont.truetype(path, size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: str, size: int,
              max_width: int):
    while size > 12:
        font = _font(font_path, size)
        if max((draw.textlength(line, font=font) for line in text.split("\n")),
               default=0) <= max_width:
            return font
        size -= 1
    return _font(font_path, 12)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    title_font = _font(FONT_BOLD, 46)
    sub_font = _font(FONT_PATH, 22)
    draw.text((80, 42), "无人机任务系统分层架构", font=title_font, fill=TEXT)
    draw.text((80, 108),
              "agent4drone（官方 Agent） + Location_amd_Navigation（导航/任务/执行） "
              "+ 本地 Qwen3-8B + MultiUAV-Plat v0.49",
              font=sub_font, fill=MUTED)

    top = 168
    gap = 20
    heights = [132, 132, 168, 148, 172, 168, 154, 160]
    label_w = 225
    x0 = 80
    content_x = x0 + label_w + 24
    content_w = W - content_x - 80

    for index, (layer, height) in enumerate(zip(LAYERS, heights)):
        y = top
        draw.rounded_rectangle((x0, y, W - 80, y + height), radius=18,
                               fill=layer["color"], outline=(210, 218, 230), width=2)
        draw.rounded_rectangle((x0, y, x0 + label_w, y + height), radius=18,
                               fill=layer["label"])
        draw.rectangle((x0 + label_w - 20, y, x0 + label_w, y + height),
                       fill=layer["label"])
        label_font = _font(FONT_BOLD, 21)
        no_font = _font(FONT_BOLD, 30)
        draw.text((x0 + 20, y + 14), layer["no"], font=no_font, fill=(255, 255, 255))
        draw.multiline_text((x0 + 20, y + 56), layer["title"], font=label_font,
                            fill=(255, 255, 255), spacing=6)

        cursor = content_x
        for offset, width, text in layer["boxes"]:
            box_x = content_x + offset
            box_y = y + 14
            box_h = height - 28
            draw.rounded_rectangle((box_x, box_y, box_x + width, box_y + box_h),
                                   radius=12, fill=(255, 255, 255),
                                   outline=(206, 214, 226), width=2)
            body_font = _fit_text(draw, text, FONT_PATH, 19, width - 28)
            draw.multiline_text((box_x + 14, box_y + 12), text, font=body_font,
                                fill=TEXT, spacing=6)
            cursor = box_x + width + 16

        if index < len(LAYERS) - 1:
            arrow_x = x0 + label_w // 2
            arrow_top = y + height + 2
            arrow_bottom = y + height + gap - 2
            draw.line((arrow_x, arrow_top, arrow_x, arrow_bottom - 4),
                      fill=ARROW, width=4)
            draw.polygon([(arrow_x - 8, arrow_bottom - 10),
                          (arrow_x + 8, arrow_bottom - 10),
                          (arrow_x, arrow_bottom + 1)], fill=ARROW)
        top += height + gap

    footer_font = _font(FONT_PATH, 19)
    draw.text((80, H - 34),
              "调用链：任务文本 → 本地 Qwen3-8B（8010→8012）→ tool_calls → "
              "任务解析（spec）→ 行为树执行（L2）→ 原子动作/规划（L3）→ REST :8000",
              font=footer_font, fill=MUTED)
    image.save(OUT)
    print("saved:", OUT, image.size)


if __name__ == "__main__":
    main()
