class ClaudeUsage < Formula
  desc "Token, cost, and session dashboard for Claude Code usage"
  homepage "https://github.com/josepe98/claude-usage"
  # URL and sha256 pinned to the current main commit.
  # Bump both when cutting a new release — see CHANGELOG.md.
  url "https://github.com/josepe98/claude-usage/archive/d4d8c78d3dbf6a72ab9bf0f91a35087f0e92f511.tar.gz"
  version "1.1.0"
  sha256 "3c554315ab7502d730d8f7ab2b62975bc9e6c0bf499b5dc3676ca8564e7b93df"
  license "MIT"
  head "https://github.com/josepe98/claude-usage.git", branch: "main"

  depends_on "python@3.13"

  def install
    libexec.install "cli.py", "scanner.py", "dashboard.py", "pricing.py", "cowork.py"

    (bin/"claude-usage").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.13"].opt_bin}/python3" "#{libexec}/cli.py" "$@"
    EOS
    chmod 0755, bin/"claude-usage"
  end

  test do
    # 1. No-args invocation prints the usage banner — exercises the shim.
    output = shell_output("#{bin}/claude-usage")
    assert_match "Claude Code Usage Dashboard", output
    assert_match "scan", output
    assert_match "dashboard", output

    # 2. `scan` against an empty projects dir exercises the real code path
    #    end-to-end (sqlite open, glob walk, summary print) without touching
    #    the user's real ~/.claude/usage.db.
    (testpath/"projects").mkpath
    scan_output = shell_output("#{bin}/claude-usage scan --projects-dir #{testpath}/projects")
    assert_match "Scan complete", scan_output
  end
end
