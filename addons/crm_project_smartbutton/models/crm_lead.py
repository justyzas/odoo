from odoo import models, fields, api, _

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    project_ids = fields.Many2many(
        'project.project',
        string='Projects',
        compute='_compute_project_ids',
    )
    project_count = fields.Integer(
        string='Project Count',
        compute='_compute_project_ids',
    )

    @api.depends('order_ids', 'order_ids.project_ids', 'order_ids.project_id', 'order_ids.order_line.project_id')
    def _compute_project_ids(self):
        for lead in self:
            orders = lead.order_ids
            if not orders:
                lead.project_ids = self.env['project.project']
                lead.project_count = 0
                continue

            # Projects linked directly to sales orders or sale order lines
            projects = orders.mapped('project_ids') | orders.mapped('project_id') | orders.mapped('order_line.project_id')

            # Search for projects linked via sale_order_id field on project.project if present
            if hasattr(self.env['project.project'], 'sale_order_id'):
                projects |= self.env['project.project'].search([('sale_order_id', 'in', orders.ids)])

            lead.project_ids = projects
            lead.project_count = len(projects)

    def action_view_projects(self):
        self.ensure_one()
        projects = self.project_ids

        action = self.env['ir.actions.actions']._for_xml_id('project.open_view_project_all')

        if len(projects) > 1:
            action['domain'] = [('id', 'in', projects.ids)]
            action['context'] = {'default_partner_id': self.partner_id.id if self.partner_id else False}
        elif len(projects) == 1:
            form_view = self.env.ref('project.edit_project', raise_if_not_found=False)
            action['views'] = [(form_view.id if form_view else False, 'form')]
            action['res_id'] = projects.id
        else:
            action = {'type': 'ir.actions.act_window_close'}

        return action
