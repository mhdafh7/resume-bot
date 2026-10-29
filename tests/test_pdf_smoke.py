import shutil
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


@unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is only installed in the container")
class PdfCompilationTests(unittest.TestCase):
    def test_example_profile_compiles_to_a_pdf(self) -> None:
        from bot import compile_latex
        from resume_core import default_tailoring_plan, parse_resume_markdown

        profile = parse_resume_markdown(
            (ROOT / "resume_data.md.example").read_text(encoding="utf-8")
        )

        pdf, diagnostic = compile_latex(profile, default_tailoring_plan(profile))

        self.assertIsNotNone(pdf, diagnostic)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 10_000)


if __name__ == "__main__":
    unittest.main()
