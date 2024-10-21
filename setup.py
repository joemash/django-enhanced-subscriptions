from setuptools import setup, find_packages

setup(
    name='django-enhanced-subscriptions',
    version='1.0.0',
    packages=find_packages(),
    include_package_data=True,
    license='MIT',
    description='This Django library provides a comprehensive solution for managing subscriptions, feature access and wallet functionality',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/joemash/django_enhanced_subscriptions',
    author='Josephat Macharia',
    author_email='josemash4@gmail.com',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 5.0',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
    python_requires='>=3.8',
    install_requires=[
        'Django>=5.0.6',
    ],
    extras_require={
        'test': [
            'pytest>=8.2.1',
            'pytest-django>=4.8.0',
            'model-bakery>=1.18.1',
            "flake8",
            "isort",
            "black",
            "tox",
            "coverage",
            "pytest-cov",
        ],
    },
)
