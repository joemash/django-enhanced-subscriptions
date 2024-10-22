Development
===========

To set up the development environment:

1. Clone the repository
2. Create a virtual environment and activate it
3. Install development dependencies:

   .. code-block:: bash

      pip install -e ".[dev]"

Run tests:
----------
   .. code-block:: bash

   pip install -e ".[test]"
   
   .. code-block:: bash

      python -m pytest tests

   OR

   .. code-block:: bash

      pytest tests/

Making migrations
------------------
.. code-block:: bash

   python testapp/manage.py makemigrations

Migrate
-------
.. code-block:: bash

   python testapp/manage.py migrate
