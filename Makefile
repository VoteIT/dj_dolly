.DEFAULT_GOAL := install

install:
	pip install -r requirements.txt
	make migrations

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

build:
	python -m build .

btest:
	if [ -d dist ]; then \
		rm -r dist; \
	fi
	python -m build .
	cd dist && tar xvfz dj_dolly*.tar.gz
