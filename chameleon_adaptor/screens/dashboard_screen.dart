/// Chameleon Adaptor — Dashboard Screen
import 'package:flutter/material.dart';

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Chameleon Hub')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('System Status', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
            const SizedBox(height: 12),
            _StatusCard(label: 'Hub', status: 'Online', color: Colors.green),
            _StatusCard(label: 'Ledger', status: 'Synced', color: Colors.teal),
            _StatusCard(label: 'Safety', status: 'Strict Mode', color: Colors.orange),
            _StatusCard(label: 'Devices', status: '3 Registered', color: Colors.blue),
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final String label;
  final String status;
  final Color color;

  const _StatusCard({required this.label, required this.status, required this.color});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: ListTile(
        leading: CircleAvatar(backgroundColor: color, radius: 8),
        title: Text(label),
        trailing: Text(status, style: TextStyle(color: color, fontWeight: FontWeight.bold)),
      ),
    );
  }
}
