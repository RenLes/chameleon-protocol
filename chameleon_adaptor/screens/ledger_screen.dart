/// Chameleon Adaptor — Ledger Screen
import 'package:flutter/material.dart';

class LedgerScreen extends StatelessWidget {
  const LedgerScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Blockchain Ledger')),
      body: const Center(
        child: Text('Ledger entries will appear here.\nConnects to Chameleon Hub /ledger endpoint.'),
      ),
    );
  }
}
