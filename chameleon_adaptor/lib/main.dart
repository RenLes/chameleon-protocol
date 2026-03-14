/// Chameleon Human Adaptor App — Flutter
/// Allows human operators to monitor, authorize, and override Chameleon device actions.

import 'package:flutter/material.dart';
import 'screens/dashboard_screen.dart';
import 'screens/device_list_screen.dart';
import 'screens/ledger_screen.dart';
import 'screens/safety_screen.dart';

void main() {
  runApp(const ChameleonApp());
}

class ChameleonApp extends StatelessWidget {
  const ChameleonApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Chameleon Adaptor',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF00BFA5),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const MainNavigation(),
    );
  }
}

class MainNavigation extends StatefulWidget {
  const MainNavigation({super.key});

  @override
  State<MainNavigation> createState() => _MainNavigationState();
}

class _MainNavigationState extends State<MainNavigation> {
  int _selectedIndex = 0;

  final List<Widget> _screens = [
    const DashboardScreen(),
    const DeviceListScreen(),
    const LedgerScreen(),
    const SafetyScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _screens[_selectedIndex],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: (index) => setState(() => _selectedIndex = index),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard), label: 'Dashboard'),
          NavigationDestination(icon: Icon(Icons.devices), label: 'Devices'),
          NavigationDestination(icon: Icon(Icons.receipt_long), label: 'Ledger'),
          NavigationDestination(icon: Icon(Icons.shield), label: 'Safety'),
        ],
      ),
    );
  }
}
