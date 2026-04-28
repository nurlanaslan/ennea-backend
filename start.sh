#!/bin/bash
python manage.py migrate
gunicorn blending_project.wsgi:application --bind 0.0.0.0:$PORT
