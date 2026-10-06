"""CrewAI 1.15.22 execution logs, isolated by run and persisted in MySQL."""

import json
from enum import Enum

from crewai import Crew
from pydantic import BaseModel, PrivateAttr

from .store import now


def serialize(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    return str(value)


class MySQLTaskOutputs:
    def __init__(self, store, run_id):
        self.store = store
        self.run_id = run_id

    def update(self, task_index, log):
        task = log["task"]
        row = {
            "task_id": str(task.id),
            "task_key": task.key,
            "expected_output": task.expected_output,
            "task_index": task_index,
            "output": log["output"],
            "inputs": log.get("inputs", {}),
            "was_replayed": log.get("was_replayed", False),
            "timestamp": now(),
        }
        with self.store.connection() as con:
            con.execute(
                "INSERT INTO crew_task_outputs VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE body=VALUES(body)",
                (
                    self.run_id,
                    task_index,
                    json.dumps(row, ensure_ascii=False, default=serialize),
                ),
            )

    def reset(self):
        with self.store.connection() as con:
            con.execute("DELETE FROM crew_task_outputs WHERE run_id=%s", (self.run_id,))

    def load(self):
        with self.store.connection() as con:
            return [
                json.loads(row[0])
                for row in con.execute(
                    "SELECT body FROM crew_task_outputs WHERE run_id=%s ORDER BY task_index",
                    (self.run_id,),
                )
            ]


class MySQLCrew(Crew):
    # Override the pinned version's private factory BEFORE Crew construction;
    # assigning after construction would already create its SQLite database.
    _task_output_handler: MySQLTaskOutputs = PrivateAttr()

    def __init__(self, *, store, run_id, **kwargs):
        super().__init__(**kwargs)
        self._task_output_handler = MySQLTaskOutputs(store, run_id)
