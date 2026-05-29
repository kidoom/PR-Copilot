from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt: str
    default_max_steps: int = 10
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)


class UnknownAgentError(Exception):
    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(f"Unknown agent type '{name}'. Available: {available}")


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, agent: AgentDefinition) -> None:
        self._agents[agent.name] = agent

    def resolve(self, name: str) -> AgentDefinition:
        agent = self._agents.get(name)
        if agent is None:
            raise UnknownAgentError(name, list(self._agents.keys()))
        return agent

    def names(self) -> list[str]:
        return list(self._agents.keys())
