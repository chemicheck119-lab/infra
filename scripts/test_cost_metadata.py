import json
import unittest
from collect_cost_metadata import service_summary


class CostMetadataTest(unittest.TestCase):
    def test_secret_values_and_account_names_are_never_exported(self):
        service = {"metadata": {"name": "synthetic-service"}, "spec": {"template": {"spec": {
            "serviceAccountName": "private@example.test", "containers": [{"env": [
                {"name": "API_KEY", "value": "synthetic-secret-canary"}]}]}}}}
        result = service_summary(service, {"bindings": [{"role": "roles/run.invoker",
            "members": ["serviceAccount:private@example.test"]}]})
        serialized = json.dumps(result)
        self.assertNotIn("canary", serialized)
        self.assertNotIn("example.test", serialized)
        self.assertFalse(result["public_invoker"])

    def test_iam_failure_is_unknown_not_private(self):
        result = service_summary({}, None)
        self.assertIsNone(result["public_invoker"])
        self.assertFalse(result["iam_available"])

    def test_service_and_revision_limits_remain_separate(self):
        result = service_summary({"metadata": {"annotations": {"run.googleapis.com/maxScale": "20"}},
            "spec": {"template": {"metadata": {"annotations": {"autoscaling.knative.dev/maxScale": "1"}}}}}, {})
        self.assertEqual(result["service_scaling"]["run.googleapis.com/maxScale"], "20")
        self.assertEqual(result["revision_scaling"]["autoscaling.knative.dev/maxScale"], "1")


if __name__ == "__main__":
    unittest.main()
