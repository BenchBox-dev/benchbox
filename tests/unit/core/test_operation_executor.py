# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.operations import OperationExecutor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestOperationExecutorInterface:
    def test_operation_executor_is_abstract(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            OperationExecutor()

    def test_operation_executor_requires_execute_operation(self):

        class IncompleteExecutor(OperationExecutor):
            def get_all_operations(self):
                return {}

            def get_operation_categories(self):
                return []

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteExecutor()

    def test_operation_executor_requires_get_all_operations(self):

        class IncompleteExecutor(OperationExecutor):
            def execute_operation(self, operation_id, connection, **kwargs):
                pass

            def get_operation_categories(self):
                return []

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteExecutor()

    def test_operation_executor_requires_get_operation_categories(self):

        class IncompleteExecutor(OperationExecutor):
            def execute_operation(self, operation_id, connection, **kwargs):
                pass

            def get_all_operations(self):
                return {}

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteExecutor()

    def test_complete_implementation_can_be_instantiated(self):

        class CompleteExecutor(OperationExecutor):
            def execute_operation(self, operation_id, connection, **kwargs):
                return {"success": True}

            def get_all_operations(self):
                return {"op1": "Operation 1"}

            def get_operation_categories(self):
                return ["category1"]

        executor = CompleteExecutor()
        assert executor is not None
        assert isinstance(executor, OperationExecutor)

    def test_isinstance_check_works(self):

        class MyExecutor(OperationExecutor):
            def execute_operation(self, operation_id, connection, **kwargs):
                return None

            def get_all_operations(self):
                return {}

            def get_operation_categories(self):
                return []

        class NotAnExecutor:
            def execute_operation(self, operation_id, connection):
                return None

        executor = MyExecutor()
        not_executor = NotAnExecutor()

        assert isinstance(executor, OperationExecutor)
        assert not isinstance(not_executor, OperationExecutor)

    def test_write_primitives_implements_interface(self):

        from benchbox import WritePrimitives

        bench = WritePrimitives(scale_factor=0.01)
        assert isinstance(bench, OperationExecutor)

    def test_operation_executor_method_signatures(self):

        import inspect

        sig = inspect.signature(OperationExecutor.execute_operation)
        params = list(sig.parameters.keys())
        assert params == ["self", "operation_id", "connection", "kwargs"]

        sig = inspect.signature(OperationExecutor.get_all_operations)
        params = list(sig.parameters.keys())
        assert params == ["self"]

        sig = inspect.signature(OperationExecutor.get_operation_categories)
        params = list(sig.parameters.keys())
        assert params == ["self"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


