"""极简行为树引擎（仅标准库）。

节点状态：SUCCESS / FAILURE / RUNNING
组合：Sequence（顺序，遇失败即失败）、Selector（选择，第一个非失败）、
      Retry（重试）、Parallel（全部成功才算成功）
叶子：Condition（返回 bool）、Action（返回 Status 或 bool）
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable


class Status(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class Node:
    name = "node"

    def tick(self, blackboard: dict) -> Status:  # pragma: no cover - 抽象
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


class Sequence(Node):
    def __init__(self, *children: Node, name: str = "sequence"):
        self.children = list(children)
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        for child in self.children:
            status = child.tick(blackboard)
            if status is not Status.SUCCESS:
                return status
        return Status.SUCCESS

    def reset(self) -> None:
        for child in self.children:
            child.reset()


class Selector(Node):
    def __init__(self, *children: Node, name: str = "selector"):
        self.children = list(children)
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        for child in self.children:
            status = child.tick(blackboard)
            if status is not Status.FAILURE:
                return status
        return Status.FAILURE

    def reset(self) -> None:
        for child in self.children:
            child.reset()


class Retry(Node):
    def __init__(self, child: Node, attempts: int = 3, name: str = "retry"):
        self.child = child
        self.attempts = max(1, int(attempts))
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        for _ in range(self.attempts):
            status = self.child.tick(blackboard)
            if status is not Status.FAILURE:
                return status
            self.child.reset()
        return Status.FAILURE

    def reset(self) -> None:
        self.child.reset()


class Condition(Node):
    def __init__(self, fn: Callable[[dict], bool], name: str = "condition"):
        self.fn = fn
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        return Status.SUCCESS if self.fn(blackboard) else Status.FAILURE


class Action(Node):
    """fn(blackboard) -> Status | bool | None（None 视作 SUCCESS）。"""

    def __init__(self, fn: Callable[[dict], Any], name: str = "action"):
        self.fn = fn
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        result = self.fn(blackboard)
        if isinstance(result, Status):
            return result
        if result is False:
            return Status.FAILURE
        return Status.SUCCESS


# ---------------------------------------------------------------- L3 动作叶子
class ActionLeaf(Node):
    """调用动作库（src/actions.py）里的一个动作的叶子节点。

    blackboard 需要: {"api": UavApi, "drone_id": str}
    可选: {"results": []} 收集每次动作结果。
    """

    def __init__(self, solution: str, params: dict | None = None,
                 name: str | None = None):
        self.action_name = solution
        self.params = dict(params or {})
        self.name = name or solution

    def tick(self, blackboard: dict) -> Status:
        from . import actions

        api = blackboard.get("api")
        drone_id = self.params.get("drone_id") or blackboard.get("drone_id")
        if api is None or not drone_id:
            blackboard.setdefault("results", []).append(
                {"ok": False, "name": self.action_name, "error": "缺少 api/drone_id"})
            return Status.FAILURE
        result = actions.run_atomic(api, str(drone_id), self.action_name, self.params)
        blackboard.setdefault("results", []).append(result)
        return Status.SUCCESS if result.get("ok") else Status.FAILURE


class WaitLeaf(Node):
    def __init__(self, seconds: float, name: str = "wait"):
        self.seconds = float(seconds)
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        sleep_fn = blackboard.get("sleep") or __import__("time").sleep
        sleep_fn(self.seconds)
        blackboard.setdefault("results", []).append(
            {"ok": True, "name": "wait", "seconds": self.seconds})
        return Status.SUCCESS


class FailLeaf(Node):
    def __init__(self, error: str, name: str = "fail"):
        self.error = str(error)
        self.name = name

    def tick(self, blackboard: dict) -> Status:
        blackboard.setdefault("results", []).append(
            {"ok": False, "name": "fail", "error": self.error})
        return Status.FAILURE


def build_tree_from_steps(steps: list[dict], name: str = "mission") -> Node:
    """把动作库展开后的原子步骤列表组装成行为树（Sequence）。"""
    children: list[Node] = []
    for step in steps or []:
        kind = str(step.get("kind") or "")
        params = dict(step.get("params") or {})
        if kind == "fail":
            children.append(FailLeaf(params.get("error") or "任务不可执行"))
        elif kind == "wait":
            children.append(WaitLeaf(float(params.get("seconds") or 5.0)))
        else:
            children.append(ActionLeaf(kind, params))
    return Sequence(*children, name=name)

