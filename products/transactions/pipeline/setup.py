"""Packaging for the transactions pipeline.

Makes the modular components a pip-installable package so the custom Dataflow
container can bake them in (and so `--setup_file ./setup.py` works as a
no-custom-container fallback). Workers then import finchat_pipeline.* natively.
"""
import setuptools

setuptools.setup(
    name="finchat_pipeline",
    version="1.0.0",
    description="FinChat transactions streaming pipeline — modular Beam components.",
    packages=setuptools.find_packages(),
    install_requires=[
        "apache-beam[gcp]==2.58.0",
        "google-cloud-dlp==3.20.0",
    ],
    python_requires=">=3.9",
)
