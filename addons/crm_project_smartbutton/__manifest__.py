{
    'name': 'CRM to Project Smart Button',
    'version': '19.0.1.0.0',
    'summary': 'Quickly access related projects directly from CRM Opportunities via Sales Orders.',
    'category': 'Sales/CRM',
    'depends': ['crm', 'sale_crm', 'sale_project', 'project'],
    'data': [
        'views/crm_lead_views.xml',
        'views/project_project_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
