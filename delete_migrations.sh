#!/bin/bash
find . -path "./dolly_testing/migrations/*.py" -not -name "__init__.py" -delete
