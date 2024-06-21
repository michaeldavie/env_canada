import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="env_canada",
    version="0.6.2",
    author="Michael Davie",
    author_email="michael.davie@gmail.com",
    description="A package to access meteorological data from Environment Canada",
    license="MIT",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/michaeldavie/env_canada",
    packages=setuptools.find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    install_requires=[
        "aiohttp>=3.9.0",
        "defusedxml",
        "geopy",
        "imageio>=2.28.0",
        "lxml",
        "numpy>=1.22.2",
        "pandas>=1.3.0",
        "Pillow>=10.0.1",
        "python-dateutil",
        "voluptuous",
    ],
    classifiers=(
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ),
)
