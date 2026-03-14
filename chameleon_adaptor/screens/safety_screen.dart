/// Chameleon Adaptor — Safety Override Screen
import 'package:flutter/material.dart';

class SafetyScreen extends StatelessWidget {
  const SafetyScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Safety Controls')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            const Text('Emergency Controls', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const SizedBox(height: 20),
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(backgroundColor: Colors.red, minimumSize: const Size.fromHeight(56)),
              icon: const Icon(Icons.stop_circle),
              label: const Text('EMERGENCY STOP ALL', style: TextStyle(fontSize: 18)),
              onPressed: () {},
            ),
            const SizedBox(height: 16),
            ElevatedButton.icon(
              style: ElevatedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
              icon: const Icon(Icons.refresh),
              label: const Text('Resume Normal Operations'),
              onPressed: () {},
            ),
          ],
        ),
      ),
    );
  }
}
