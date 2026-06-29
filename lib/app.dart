import 'package:flutter/material.dart';

import 'presentation/pages/home_page.dart';

/// 应用根 Widget：主题 + 首页。
class K230App extends StatelessWidget {
  const K230App({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'K230 联机',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}
