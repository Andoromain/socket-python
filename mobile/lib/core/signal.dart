/// Signal synchrone, équivalent des signaux Qt de la version PC.
///
/// Les écouteurs sont appelés immédiatement, dans l'ordre d'inscription, ce qui
/// conserve l'ordre des événements de la logique Python transposée telle quelle.
class Signal<T> {
  final List<void Function(T)> _listeners = [];

  void connect(void Function(T value) listener) => _listeners.add(listener);

  void disconnect(void Function(T value) listener) => _listeners.remove(listener);

  void emit(T value) {
    for (final listener in List.of(_listeners)) {
      listener(value);
    }
  }
}
