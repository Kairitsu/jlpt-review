import 'package:flutter/material.dart';

import '../app/router.dart';

class PageShell extends StatelessWidget {
  const PageShell({
    required this.title,
    required this.child,
    this.subtitle,
    this.actions = const [],
    super.key,
  });

  final String title;
  final String? subtitle;
  final Widget child;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    final currentRoute = ModalRoute.of(context)?.settings.name ?? AppRoute.home;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title),
            if (subtitle != null)
              Text(
                subtitle!,
                style: Theme.of(context).textTheme.bodySmall,
              ),
          ],
        ),
        actions: actions,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex(currentRoute),
        onDestinationSelected: (index) => _navigateToTab(context, _routes[index]),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), label: '首页'),
          NavigationDestination(icon: Icon(Icons.menu_book_outlined), label: '句库'),
          NavigationDestination(icon: Icon(Icons.tune), label: '设置'),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
          children: [child],
        ),
      ),
    );
  }

  static const _routes = [AppRoute.home, AppRoute.library, AppRoute.settings];

  void _navigateToTab(BuildContext context, String routeName) {
    final currentRoute = ModalRoute.of(context)?.settings.name ?? AppRoute.home;
    if (currentRoute == routeName) return;

    Navigator.of(context).pushNamedAndRemoveUntil(routeName, (route) => false);
  }

  int _selectedIndex(String routeName) {
    if (routeName.startsWith(AppRoute.library)) return 1;
    if (routeName.startsWith(AppRoute.settings)) return 2;
    return 0;
  }
}
