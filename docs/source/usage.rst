Usage
=====

.. _installation:

Installation
============

.. code-block:: bash

   pip install django-enhanced-subcriptions

Quick start
===========

1. Add "django-enhanced-subcriptions" to your ``INSTALLED_APPS`` setting:

   .. code-block:: python

      INSTALLED_APPS = [
          ...
          'django-enhanced-subcriptions',
      ]

2. Run migrations:

   .. code-block:: bash

      python manage.py migrate

Development
===========

To set up the development environment:

1. Clone the repository
2. Create a virtual environment and activate it
3. Install development dependencies:

   .. code-block:: bash

      pip install -e ".[dev]"

4. Run tests:

   .. code-block:: bash

      python -m pytest tests

   OR

   .. code-block:: bash

      pytest tests/

Install test dependencies
==========================

.. code-block:: bash

   pip install -e ".[test]"

Making migrations
=================

.. code-block:: bash

   python testapp/manage.py makemigrations

   python testapp/manage.py makemigrations <app_name> --empty

Migrate
=======

.. code-block:: bash

   python testapp/manage.py migrate
