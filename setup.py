from setuptools import setup, find_packages

setup(
    name="personal-agent",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "sage=app.main:run_sage",
        ],
    },
)
