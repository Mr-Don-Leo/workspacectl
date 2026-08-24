import os
import subprocess
import tempfile
import unittest

from devws.services import gitinfo


def git(cwd, *args):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    subprocess.run(
        ["git", "-C", cwd, *args], check=True, capture_output=True, env=env
    )


class GitStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")

    def commit_file(self, name, content="x"):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(content)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", f"add {name}")

    def test_non_repo_returns_none(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        self.assertIsNone(gitinfo.status(plain))
        self.assertFalse(gitinfo.is_repo(plain))

    def test_clean_repo(self):
        self.commit_file("a.txt")
        status = gitinfo.status(self.repo)
        self.assertEqual(status["branch"], "main")
        self.assertTrue(status["clean"])
        self.assertEqual(status["last_commit"]["subject"], "add a.txt")
        self.assertTrue(gitinfo.is_repo(self.repo))

    def test_empty_repo_reports_branch_without_commits(self):
        status = gitinfo.status(self.repo)
        self.assertEqual(status["branch"], "main")
        self.assertIsNone(status["last_commit"])

    def test_dirty_counts(self):
        self.commit_file("a.txt")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("changed")  # unstaged modification
        with open(os.path.join(self.repo, "new.txt"), "w") as fh:
            fh.write("new")  # untracked
        with open(os.path.join(self.repo, "staged.txt"), "w") as fh:
            fh.write("staged")
        git(self.repo, "add", "staged.txt")  # staged addition

        status = gitinfo.status(self.repo)
        self.assertFalse(status["clean"])
        self.assertEqual(status["unstaged"], 1)
        self.assertEqual(status["untracked"], 1)
        self.assertEqual(status["staged"], 1)

    def test_ahead_behind_against_remote(self):
        self.commit_file("a.txt")
        remote = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        git(self.repo, "remote", "add", "origin", remote)
        git(self.repo, "push", "-q", "-u", "origin", "main")

        self.commit_file("b.txt")  # one commit ahead of origin
        status = gitinfo.status(self.repo)
        self.assertEqual(status["ahead"], 1)
        self.assertEqual(status["behind"], 0)

        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")  # now one behind origin
        status = gitinfo.status(self.repo)
        self.assertEqual(status["ahead"], 0)
        self.assertEqual(status["behind"], 1)


class PorcelainParserTests(unittest.TestCase):
    def test_parses_branch_with_ahead_and_behind(self):
        text = "## main...origin/main [ahead 2, behind 1]\n M devws/server.py\n?? new.txt\n"
        parsed = gitinfo.parse_porcelain_status(text)
        self.assertEqual(parsed["branch"], "main")
        self.assertEqual(parsed["ahead"], 2)
        self.assertEqual(parsed["behind"], 1)
        self.assertEqual(parsed["unstaged"], 1)
        self.assertEqual(parsed["untracked"], 1)
        self.assertFalse(parsed["clean"])

    def test_detached_head(self):
        parsed = gitinfo.parse_porcelain_status("## HEAD (no branch)\n")
        self.assertEqual(parsed["branch"], "(detached)")
        self.assertTrue(parsed["clean"])

    def test_conflict_markers(self):
        parsed = gitinfo.parse_porcelain_status("## main\nUU merge.txt\nAA both.txt\n")
        self.assertEqual(parsed["conflicts"], 2)
        self.assertEqual(parsed["staged"], 0)

    def test_staged_and_unstaged_same_file(self):
        parsed = gitinfo.parse_porcelain_status("## main\nMM file.txt\n")
        self.assertEqual(parsed["staged"], 1)
        self.assertEqual(parsed["unstaged"], 1)


if __name__ == "__main__":
    unittest.main()
