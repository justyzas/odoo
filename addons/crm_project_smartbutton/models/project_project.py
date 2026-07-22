from odoo import models, fields, api

class ProjectProject(models.Model):
    _inherit = 'project.project'

    lead_ids = fields.Many2many(
        'crm.lead',
        string='Related Leads',
        compute='_compute_lead_ids'
    )
    lead_count = fields.Integer(
        string='Lead Count',
        compute='_compute_lead_ids'
    )

    @api.depends('sale_order_id.opportunity_id', 'sale_line_id.order_id.opportunity_id')
    def _compute_lead_ids(self):
        for project in self:
            orders = self.env['sale.order']
            if getattr(project, 'sale_order_id', False):
                orders |= project.sale_order_id
            if getattr(project, 'sale_line_id', False):
                orders |= project.sale_line_id.order_id
                
            orders |= self.env['sale.order'].search([('project_id', '=', project.id)])

            leads = orders.mapped('opportunity_id')
            project.lead_ids = leads
            project.lead_count = len(leads)

    def action_view_leads(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('crm.crm_lead_all_leads')
        if self.lead_count > 1:
            action['domain'] = [('id', 'in', self.lead_ids.ids)]
            action['context'] = {'default_type': 'opportunity'}
        elif self.lead_count == 1:
            form_view = self.env.ref('crm.crm_lead_view_form', raise_if_not_found=False)
            action['views'] = [(form_view.id if form_view else False, 'form')]
            action['res_id'] = self.lead_ids.id
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action
