# Third-party notices

Original repository additions use [MIT](LICENSE). Inherited pythonTemplate source
retains its [MIT notice](LICENSES/pythonTemplate-MIT.txt). Generated projects
choose their own license and preserve scaffold attribution.

`uv.lock` pins maintenance dependencies; generated runtime locks belong to each
project. Copier/Jinja, uv, Hatchling/scikit-build-core, pybind11, Ruff, mypy, pytest
and Typer keep their upstream distribution licenses. FastAPI, Starlette, Uvicorn,
Pydantic, prometheus-client and prometheus-fastapi-instrumentator are distributed
under their upstream permissive terms, not relicensed by this repository. The
optional SQLAlchemy/asyncpg/Alembic stack keeps its upstream terms. Base image,
PostgreSQL, Helm and Kubernetes distribution notices remain applicable. Private
SDK builds retain the SDK's MIT license in installed package metadata.

No monitoring image is redistributed as MIT. Future project assets/dependencies
need their own review; license selection does not change visibility or data access.
