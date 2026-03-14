/// Chameleon Adaptor — Device List Screen
import 'package:flutter/material.dart';

class DeviceListScreen extends StatelessWidget {
  const DeviceListScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final devices = [
      {'id': 'CHA-KIT-001', 'name': 'Stovetop Kettle', 'class': 'kitchen.appliance.kettle', 'status': 'idle'},
      {'id': 'CHA-SEC-001', 'name': 'Smart Door Lock', 'class': 'security.access.door_lock', 'status': 'locked'},
      {'id': 'CHA-HLT-001', 'name': 'Medication Dispenser', 'class': 'healthcare.dispenser.medication', 'status': 'standby'},
    ];

    return Scaffold(
      appBar: AppBar(title: const Text('Registered Devices')),
      body: ListView.builder(
        itemCount: devices.length,
        itemBuilder: (ctx, i) {
          final d = devices[i];
          return Card(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: ListTile(
              title: Text(d['name']!),
              subtitle: Text('${d['id']} · ${d['class']}'),
              trailing: Chip(label: Text(d['status']!)),
              onTap: () {},
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {},
        child: const Icon(Icons.add),
        tooltip: 'Register Device',
      ),
    );
  }
}
