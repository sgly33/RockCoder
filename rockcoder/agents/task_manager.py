 
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from rockcoder.agent import Agent

log = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_activity: str = ""


@dataclass
class BackgroundTask:
    id: str
    name: str
    agent: Agent
    task: str
    status: str = "running"
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)


class TaskManager:


    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}


    def launch(
        self,
        agent: Agent,
        task: str,
        name: str = "",
        fork_conversation: Any = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(
            self._run_background(task_id, fork_conversation)
        )
        self._async_tasks[task_id] = async_task

        bg.cancel = async_task.cancel
        return task_id


    async def _run_background(
        self, task_id: str, fork_conversation: Any = None
    ) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            if fork_conversation is not None:
                result = await bg.agent.run_to_completion("", fork_conversation)
            else:
                result = await bg.agent.run_to_completion(bg.task)
            bg.result = result
            bg.status = "completed"

            if bg.agent.team_name and bg.agent._team_manager:
                mailbox = bg.agent._team_manager.get_mailbox(bg.agent.team_name)
                if mailbox:
                    from rockcoder.teams.mailbox import create_message
                    msg = create_message(
                        from_agent=bg.name,
                        to_agent="lead",
                        content=f"[idle] {bg.name}: completed initial task",
                        summary=f"{bg.name} idle",
                    )
                    mailbox.write("lead", msg)

        except asyncio.CancelledError:
            bg.status = "cancelled"
            bg.result = "Task was cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            bg.end_time = time.monotonic()
            bg.progress.input_tokens = bg.agent.total_input_tokens
            bg.progress.output_tokens = bg.agent.total_output_tokens
            self._async_tasks.pop(task_id, None)
            await self._notify_queue.put(task_id)


    def adopt_running(
        self,
        agent: Agent,
        task_description: str,
        partial_result: str = "",
        name: str = "",
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
            result=partial_result,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(self._continue_background(task_id))
        self._async_tasks[task_id] = async_task
        bg.cancel = async_task.cancel
        return task_id


    async def _continue_background(self, task_id: str) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            result = await bg.agent.run_to_completion(bg.task)
            bg.result = (bg.result + "\n" + result).strip() if bg.result else result
            bg.status = "completed"
        except asyncio.CancelledError:
            bg.status = "cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            bg.end_time = time.monotonic()
            bg.progress.input_tokens = bg.agent.total_input_tokens
            bg.progress.output_tokens = bg.agent.total_output_tokens
            self._async_tasks.pop(task_id, None)
            await self._notify_queue.put(task_id)

    def adopt_teammate_handle(
        self,
        handle: Any,
        agent: Agent,
        task_description: str,
        name: str = "",
    ) -> str:
        """接管一个已经由spawn_inprocess_teammate启动的teammate。

        让TaskManager跟踪外部启动的teammate，以便UI可以通过poll_completed()检测完成。

        Args:
            handle: InProcessTeammateHandle对象
            agent: Agent实例
            task_description: 任务描述
            name: 任务名称

        Returns:
            task_id: 任务ID
        """
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
        )
        self._tasks[task_id] = bg

        # 注册teammate的asyncio.Task
        self._async_tasks[task_id] = handle.task

        # 设置cancel回调
        bg.cancel = handle.cancel

        # 启动监控任务
        asyncio.create_task(self._monitor_teammate(task_id, handle))

        log.info("Adopted teammate handle: task_id=%s name=%s", task_id, name)
        return task_id

    async def _monitor_teammate(self, task_id: str, handle: Any) -> None:
        """监控teammate的完成状态，完成后发送通知。

        Args:
            task_id: 任务ID
            handle: InProcessTeammateHandle对象
        """
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            # 等待teammate的task完成
            result = await handle.task
            bg.result = result or ""
            bg.status = "completed"
            log.info("Teammate %s completed", task_id)
        except asyncio.CancelledError:
            bg.status = "cancelled"
            bg.result = "Teammate was cancelled"
            log.info("Teammate %s cancelled", task_id)
        except Exception as e:
            log.error("Teammate task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            bg.end_time = time.monotonic()

            # 更新token统计
            bg.progress.input_tokens = bg.agent.total_input_tokens
            bg.progress.output_tokens = bg.agent.total_output_tokens

            # 移除async_task引用
            self._async_tasks.pop(task_id, None)

            # 发送完成通知
            await self._notify_queue.put(task_id)

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        bg = self._tasks.get(task_id)
        if bg is None or bg.status != "running":
            return False
        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        return False

    def poll_completed(self) -> list[BackgroundTask]:
        completed: list[BackgroundTask] = []
        while not self._notify_queue.empty():
            try:
                task_id = self._notify_queue.get_nowait()
                bg = self._tasks.get(task_id)
                if bg is not None:
                    completed.append(bg)
            except asyncio.QueueEmpty:
                break
        return completed
