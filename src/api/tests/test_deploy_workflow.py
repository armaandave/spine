"""Regression tests for the self-hosted desk Mac deploy workflow."""

from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-backend-desk-mac.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FEATURED_LIST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync-featured-lists.yml"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "codex-mobile-deploy-backend.sh"


class DeskMacDeployWorkflowTests(SimpleTestCase):
    """Ensure backend deploys cannot mutate the source branch."""

    def test_workflow_cannot_write_to_repository(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_deploy_backs_up_database_before_replacing_app(self):
        workflow = WORKFLOW.read_text()
        self.assertLess(workflow.index("pg_dump"), workflow.index("./scripts/codex-mobile-deploy-backend.sh"))
        self.assertIn("pg_restore --list", workflow)
        self.assertIn("set -o noclobber", workflow)
        self.assertIn("migrate --check", workflow)
        self.assertIn('[[ "$deployed_sha" == "$expected_sha" ]]', workflow)

    def test_deploy_script_never_updates_github_branch(self):
        script = DEPLOY_SCRIPT.read_text()

        forbidden_commands = [
            "git push",
            "git pull",
            "push origin",
            "git merge",
            "git rebase",
        ]
        for command in forbidden_commands:
            with self.subTest(command=command):
                self.assertNotIn(command, script)

    def test_deploy_script_uses_isolated_checkout(self):
        script = DEPLOY_SCRIPT.read_text()

        self.assertIn('repo_dir="${SPINE_DEPLOY_DIR:-$HOME/projects/spine-deploy}"', script)
        self.assertIn('source_repo_dir="${SPINE_SOURCE_REPO_DIR:-$HOME/projects/spine}"', script)
        self.assertIn("Deploy directory must be separate from source repo", script)
        self.assertIn('git fetch origin "+refs/heads/$branch:refs/remotes/origin/$branch"', script)
        self.assertIn('target="origin/$branch"', script)
        self.assertIn('git reset --hard "$target"', script)

    def test_deploy_script_only_deploys_commits_on_the_branch(self):
        script = DEPLOY_SCRIPT.read_text()

        self.assertIn('commit="${2:-}"', script)
        self.assertIn("^[0-9a-f]{7,40}$", script)
        self.assertIn('--contains "$commit" --list "origin/$branch"', script)

    def test_ci_deploys_only_checked_pushes_to_myapp_main(self):
        ci = CI_WORKFLOW.read_text()

        self.assertIn("permissions:\n  contents: read", ci)
        self.assertNotIn("contents: write", ci)
        self.assertIn("needs: [checks, database]", ci)
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == 'refs/heads/myapp-main'",
            ci,
        )
        self.assertIn("uses: ./.github/workflows/deploy-backend-desk-mac.yml", ci)
        self.assertIn("ref: ${{ github.sha }}", ci)

    def test_push_deploys_skip_stale_and_frontend_only_pushes(self):
        workflow = WORKFLOW.read_text()

        self.assertIn('if [[ "$tip" != "$REQUESTED_REF" ]]; then', workflow)
        self.assertIn('diff --quiet "$deployed" "$REQUESTED_REF" --', workflow)
        # The backup, the deploy and the verification all wait on that decision.
        self.assertEqual(workflow.count("if: steps.plan.outputs.deploy == 'true'"), 3)
        self.assertLess(workflow.index("id: plan"), workflow.index("pg_dump"))

    def test_featured_list_sync_is_fixed_and_production_guarded(self):
        workflow = FEATURED_LIST_WORKFLOW.read_text()

        self.assertIn("group: spine-production-mutation", workflow)
        self.assertIn("confirm_production_write", workflow)
        self.assertIn('[[ "$deployed_sha" == "$EXPECTED_SHA" ]]', workflow)
        self.assertIn(
            'source_url="https://mdblist.com/lists/davearmaan12/external/155837"',
            workflow,
        )
        self.assertIn("--owner Spine", workflow)
        self.assertIn("nyt_bestsellers", workflow)
        self.assertIn("sync_nyt_featured_lists", workflow)
        self.assertNotIn("source_url: ${{", workflow)

    def test_deploy_requires_nyt_key_and_queues_background_sync(self):
        script = DEPLOY_SCRIPT.read_text()

        self.assertIn("Missing or empty NYT_BOOKS_API_KEY", script)
        self.assertIn("sync_nyt_featured_lists.delay()", script)
        self.assertIn("for attempt in 1 2 3", script)
