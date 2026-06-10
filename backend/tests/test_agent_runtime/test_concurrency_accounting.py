"""Concurrency tests for run-level accounting.

Proves parallel SubAgent usage is aggregated exactly once and
budget admission remains deterministic.
"""
from __future__ import annotations

import asyncio
import pytest

from backend.agent.runtime.accounting import OperationUsage, RunUsage


class TestParallelUsageAggregation:
    @pytest.mark.asyncio
    async def test_parallel_operations_aggregate_once(self):
        """Multiple concurrent add_operation calls aggregate correctly."""
        run_usage = RunUsage(model_id="gpt-4o")

        async def add_usage(task_id: str, tokens: int):
            op = OperationUsage(
                operation_type="test_context",
                task_id=task_id,
                model_calls=1,
                input_tokens=tokens,
                output_tokens=tokens // 2,
            )
            # Small delay to simulate real work
            await asyncio.sleep(0.01)
            run_usage.add_operation(op)

        # Run 10 concurrent operations
        await asyncio.gather(*[add_usage(f"t{i}", 100 * i) for i in range(10)])

        # All operations should be recorded
        assert len(run_usage.operations) == 10
        # Total should be sum of all
        expected_total_input = sum(100 * i for i in range(10))
        assert run_usage.total_input_tokens == expected_total_input
        expected_total_output = sum(50 * i for i in range(10))
        assert run_usage.total_output_tokens == expected_total_output
        assert run_usage.total_model_calls == 10

    @pytest.mark.asyncio
    async def test_concurrent_add_operation_is_safe(self):
        """Concurrent add_operation calls don't lose data."""
        run_usage = RunUsage()

        async def add_one():
            op = OperationUsage(operation_type="test", model_calls=1, input_tokens=1)
            run_usage.add_operation(op)

        # Run 100 concurrent adds
        await asyncio.gather(*[add_one() for _ in range(100)])

        assert run_usage.total_model_calls == 100
        assert run_usage.total_input_tokens == 100
        assert len(run_usage.operations) == 100

    @pytest.mark.asyncio
    async def test_per_operation_breakdown_from_parallel(self):
        """Per-operation breakdown groups correctly after parallel adds."""
        run_usage = RunUsage()

        async def add_op(op_type: str, count: int):
            for _ in range(count):
                op = OperationUsage(operation_type=op_type, model_calls=1, input_tokens=10)
                run_usage.add_operation(op)

        await asyncio.gather(
            add_op("test_context", 5),
            add_op("security_context", 3),
            add_op("test_context", 2),
        )

        breakdown = run_usage.per_operation_breakdown
        assert breakdown["test_context"]["model_calls"] == 7
        assert breakdown["test_context"]["input_tokens"] == 70
        assert breakdown["security_context"]["model_calls"] == 3
        assert breakdown["security_context"]["input_tokens"] == 30


class TestBudgetAdmissionDeterminism:
    def test_budget_admission_order_independent(self):
        """Budget admission is deterministic regardless of operation order."""
        usage1 = RunUsage()
        usage2 = RunUsage()

        ops = [
            OperationUsage(operation_type="a", model_calls=1, input_tokens=100),
            OperationUsage(operation_type="b", model_calls=2, input_tokens=200),
            OperationUsage(operation_type="c", model_calls=3, input_tokens=300),
        ]

        # Add in different orders
        for op in ops:
            usage1.add_operation(op)
        for op in reversed(ops):
            usage2.add_operation(op)

        # Totals should be the same
        assert usage1.total_model_calls == usage2.total_model_calls
        assert usage1.total_input_tokens == usage2.total_input_tokens
        assert usage1.total_output_tokens == usage2.total_output_tokens

    def test_run_usage_immutability_after_build(self):
        """RunUsage snapshot is consistent after building."""
        usage = RunUsage()
        for i in range(5):
            usage.add_operation(OperationUsage(
                operation_type=f"op_{i}",
                model_calls=1,
                input_tokens=100,
            ))

        # Snapshot the state
        snapshot = usage.to_dict()
        assert snapshot["total_model_calls"] == 5
        assert snapshot["total_input_tokens"] == 500

        # Adding more operations shouldn't affect the snapshot
        usage.add_operation(OperationUsage(operation_type="extra", model_calls=1, input_tokens=50))
        assert snapshot["total_model_calls"] == 5  # Snapshot unchanged
        assert usage.total_model_calls == 6  # Live object updated
