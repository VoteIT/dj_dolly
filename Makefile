.DEFAULT_GOAL := install

install:
	pip install -r requirements.txt

coverage:
	coverage run ./manage.py test dolly dolly_testing --keepdb && coverage report

migrations:
	./manage.py makemigrations

migrate:
	./manage.py migrate

run:
	./manage.py runserver

test:
	./manage.py test dolly dolly_testing --failfast

cover:
	coverage run ./manage.py test dolly dolly_testing --failfast && coverage report

notebook:
	./manage.py shell_plus --notebook
