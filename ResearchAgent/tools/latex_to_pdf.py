import os
import subprocess
from typing import Tuple

def latex_to_pdf(tex_source: str, output_dir: str = "outputs") -> Tuple[bool, str]:
    """
    Compiles LaTeX source text into PDF, returns (success, path_or_error_message).

    Args:
      tex_source: The full .tex content as a string.
      output_dir: Directory inside the repo in which to save the files.

    Returns:
      A tuple (bool, str):
        - True, path to PDF if successful
        - False, error message otherwise
    """
    os.makedirs(output_dir, exist_ok=True)
    tex_path = os.path.join(output_dir, "document.tex")
    pdf_path = os.path.join(output_dir, "document.pdf")

    try:
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)
    except Exception as e:
        return False, f"Error writing .tex file: {e}"

    try:
        # Using pdflatex; ensure it's installed
        subprocess.run(
            ["/Library/TeX/texbin/pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory", output_dir, tex_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        return False, f"LaTeX compile error: {e.stderr.decode('utf-8', errors='ignore')}"

    if os.path.exists(pdf_path):
        return True, pdf_path
    else:
        return False, "PDF not generated."
