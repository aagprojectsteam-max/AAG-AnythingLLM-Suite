import unittest
import yaml
from release_gate import IntegrityError
from release_mounts import deployed,set_image

TEXT = """# kept comment
services:
  anythingllm:
    image: example/app@sha256:aaa
    restart: unless-stopped
  portainer:
    image: example/other:1
"""


class ImageSwitch(unittest.TestCase):
    def test_changes_only_the_service_image_and_keeps_comments(self):
        out = set_image(TEXT, "example/app@sha256:aaa", "example/app@sha256:bbb")
        self.assertIn("# kept comment", out)
        data = yaml.safe_load(out)["services"]
        self.assertEqual(data["anythingllm"]["image"], "example/app@sha256:bbb")
        self.assertEqual(data["portainer"]["image"], "example/other:1")

    def test_refuses_an_image_that_also_appears_elsewhere(self):
        with self.assertRaises(IntegrityError):
            set_image(TEXT + "# example/app@sha256:aaa\n", "example/app@sha256:aaa", "example/app@sha256:bbb")

    def test_forward_plan_uses_target_and_rollback_plan_keeps_original(self):
        plan = {"image": "old", "image_id": "1", "target_image": "new", "target_image_id": "2"}
        self.assertEqual(deployed(plan)["image"], "new")
        self.assertEqual(deployed(plan)["image_id"], "2")
        self.assertEqual(plan["image"], "old")
        self.assertEqual(deployed({"image": "old", "image_id": "1"})["image"], "old")


if __name__ == "__main__":
    unittest.main()
