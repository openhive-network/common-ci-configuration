# Common CI Configuration

This project contains the common CI templates and scripts.

## Directory structure

- misc - miscellaneous files
- scripts/bash - Bash scripts
- scripts/python - Python scripts
- templates - GitLab CI templates

## Tmp

docker build --target docker-builder --tag docker-builder .
docker build --target image-remover --tag image-remover .
