{
    'name': 'Dark Theme (Community)',
    'version': '1.0',
    'category': 'Theme',
    'summary': 'Enable a simple global dark mode for Odoo Community.',
    'description': 'Adds a custom CSS layer that turns Odoo into a dark-themed interface.',
    'author': 'Your Name',
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'dark_theme/static/src/css/dark.css',
        ],
        'web.assets_frontend': [
            'dark_theme/static/src/css/dark.css',
        ],
    },
    'installable': True,
    'application': False,
}
