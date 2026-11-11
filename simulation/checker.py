"""Headless execution-check evaluator from the upstream MultiUAV-Plat controller."""


class ExecutionChecker:
    def __init__(self, api_server):
        self.api_server = api_server

    def evaluate_execution_check_node(self, node, session_id, depth=0):
        """
        Recursively evaluate an execution check node (group or leaf).
        Reuses logic from gui_controller.py

        Returns:
            tuple: (result: bool, details: list of dict)
        """
        if not isinstance(node, dict):
            return False, [{"error": "Invalid node structure", "depth": depth}]

        # Check if this is a group node (has 'checks' array)
        if 'checks' in node:
            logic = node.get('logic', 'and').lower()
            checks = node.get('checks') or []

            details = []
            results = []

            # Add group header to details
            details.append({
                "type": "group",
                "logic": logic.upper(),
                "depth": depth,
                "count": len(checks)
            })

            # Evaluate each child check
            for check in checks:
                child_result, child_details = self.evaluate_execution_check_node(check, session_id, depth + 1)
                results.append(child_result)
                details.extend(child_details)

            # Apply logical operator
            if logic == 'and':
                final_result = all(results) if results else True
            elif logic == 'or':
                final_result = any(results) if results else False
            elif logic == 'not':
                final_result = not all(results) if results else True
            else:
                final_result = False
                details.append({
                    "type": "error",
                    "message": f"Unknown logic operator: {logic}",
                    "depth": depth
                })

            return final_result, details

        # This is a leaf node - make API call
        endpoint = node.get('endpoint') or node.get('path', '')
        parameters = node.get('parameters', {})
        expect = node.get('expect', True)

        # Ensure expect is boolean
        if isinstance(expect, dict) and 'result' in expect:
            expect = bool(expect.get('result'))
        elif not isinstance(expect, bool):
            expect = True

        # Make the check API call
        try:
            params_with_session = dict(parameters)
            if 'session_id' not in params_with_session and session_id:
                params_with_session['session_id'] = session_id

            response = self.api_server.api_check_execution(endpoint, params_with_session, show_error=False)

            if response is None:
                return False, [{
                    "type": "leaf",
                    "endpoint": endpoint,
                    "parameters": parameters,
                    "expect": expect,
                    "actual": None,
                    "result": False,
                    "error": "API call failed or returned None",
                    "depth": depth
                }]

            actual_result = response.get('result', False) if isinstance(response, dict) else False
            check_passed = (actual_result == expect)

            return check_passed, [{
                "type": "leaf",
                "endpoint": endpoint,
                "parameters": parameters,
                "expect": expect,
                "actual": actual_result,
                "result": check_passed,
                "response": response,
                "depth": depth
            }]

        except Exception as e:
            return False, [{
                "type": "leaf",
                "endpoint": endpoint,
                "parameters": parameters,
                "expect": expect,
                "actual": None,
                "result": False,
                "error": str(e),
                "depth": depth
            }]
