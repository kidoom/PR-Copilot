"""Tests for compact prompt profiles."""
from __future__ import annotations

import pytest

from backend.agent.runtime.compression.compact_prompts import (
    MAIN_AGENT_COMPACT_PROMPT,
    SUBAGENT_COMPACT_PROMPT,
    CompactProfile,
    compact_user_prompt,
    get_compact_profile_prompt,
    select_compact_profile,
)


class TestCompactProfile:
    def test_main_agent_profile(self):
        assert CompactProfile.MAIN_AGENT == "main_agent"

    def test_subagent_profile(self):
        assert CompactProfile.SUBAGENT == "subagent"


class TestProfileSelection:
    def test_main_session_selects_main_profile(self):
        profile = select_compact_profile("main")
        assert profile == CompactProfile.MAIN_AGENT

    def test_subagent_session_selects_subagent_profile(self):
        profile = select_compact_profile("subagent")
        assert profile == CompactProfile.SUBAGENT

    def test_subagent_with_type_selects_subagent_profile(self):
        profile = select_compact_profile("subagent", "security-context-agent")
        assert profile == CompactProfile.SUBAGENT


class TestProfilePrompts:
    def test_main_agent_prompt_not_empty(self):
        prompt = get_compact_profile_prompt(CompactProfile.MAIN_AGENT)
        assert len(prompt) > 0
        assert "PR review coordinator" in prompt

    def test_subagent_prompt_not_empty(self):
        prompt = get_compact_profile_prompt(CompactProfile.SUBAGENT)
        assert len(prompt) > 0
        assert "subagent" in prompt

    def test_main_agent_prompt_preserves_key_elements(self):
        prompt = get_compact_profile_prompt(CompactProfile.MAIN_AGENT)
        assert "run_id" in prompt
        assert "Planner state" in prompt
        assert "Subagent results" in prompt

    def test_subagent_prompt_preserves_key_elements(self):
        prompt = get_compact_profile_prompt(CompactProfile.SUBAGENT)
        assert "task" in prompt
        assert "evidence" in prompt
        assert "Repository context" in prompt


class TestUserPrompt:
    def test_main_agent_user_prompt(self):
        prompt = compact_user_prompt(20, CompactProfile.MAIN_AGENT)
        assert "20 messages" in prompt
        assert "PR review session" in prompt

    def test_subagent_user_prompt(self):
        prompt = compact_user_prompt(15, CompactProfile.SUBAGENT)
        assert "15 messages" in prompt
        assert "subagent" in prompt
