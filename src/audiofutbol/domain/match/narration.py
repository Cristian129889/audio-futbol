"""Anti-repetition Spanish match commentary generator with rich tactical variety."""

from __future__ import annotations

from collections import deque
import random


TEMPLATES: dict[str, tuple[str, ...]] = {
    # ---- Defensive Block (6 distinct outcomes, zero boring repetition)
    "block_intercept": (
        "¡Bloque infranqueable! {def_} lee el pase de {riv} e intercepta limpio.",
        "{def_} cierra la línea de pase en bloque y roba el balón sin hacer falta.",
        "¡Qué lectura de {def_}! Corta el envío interior de {riv} y lanza a tu equipo.",
        "El bloque compacto asfixia a {riv}: {def_} se anticipa y recupera la posesión.",
        "No hay hueco por dentro. {def_} mete la pierna a tiempo y le roba el balón a {riv}.",
        "Tu defensa bascula perfecta y {def_} intercepta el balón filtrado por {riv}.",
    ),
    "block_push_back": (
        "Tu bloque defensivo cierra todos los carriles y obliga a {rival} a retroceder {zona_r}.",
        "Sin espacios para progresar, {riv} se ve encerrado por {def_} y tiene que jugar hacia atrás, {zona_r}.",
        "Muralla bien ordenada: {rival} pierde metros ante la presión del bloque y vuelve {zona_r}.",
        "Tu línea defensiva da un paso firme; {rival} no encuentra salida y recula {zona_r}.",
        "¡Gran trabajo táctico! {def_} tapa la diagonal y {rival} retrocede con el balón {zona_r}.",
    ),
    "block_force_wide": (
        "El bloque empuja a {riv} contra la banda {banda}; {def_} lo encierra y el balón sale fuera. ¡Saque de banda para ti!",
        "Sin pase por el centro, {riv} se queda sin cancha por la banda {banda} y regala la posesión.",
        "{def_} lleva a {riv} hacia la línea de cal por la banda {banda} y provoca que pierda el balón.",
        "Cierre perfecto hacia la banda {banda}: {rival} queda acorralado y recuperas el saque.",
    ),
    "block_desperate_shot": (
        "Sin encontrar huecos en tu bloque, {riv} se desespera y prueba un remate forzado desde lejos.",
        "Tu defensa no deja entrar al área; {riv} intenta romper el cerrojo con un tiro lejano.",
        "El bloque aguanta firme y obliga a {riv} a disparar sin ángulo desde fuera.",
    ),
    "block_shot_blocked": (
        "¡Paredón de {def_}! Se lanza a tapar el disparo de {riv} y tu equipo recupera el rebote.",
        "El remate de {riv} se estrella en el cuerpo de {def_}, bien plantado en el bloque, y despejas el peligro.",
        "{def_} achica justo a tiempo, bloquea el tiro de {riv} y sales jugando con tranquilidad.",
    ),
    "block_hold_solid": (
        "Tu equipo bascula en bloque con orden; {rival} toca de lado a lado sin romper líneas y empieza a impacientarse.",
        "Defensa muy junta entre centrales y medios: {riv} busca un hueco, pero {def_} le cierra el paso.",
        "El bloque mantiene las distancias cortas; {rival} desgasta el reloj moviendo el balón sin profundidad.",
        "Orden táctico total: tus líneas se compactan y {rival} choca una y otra vez contra el muro.",
        "{def_} ordena a sus compañeros desde atrás; {rival} no encuentra filtro y acumula frustración.",
    ),
    "block_broken": (
        "{riv} encuentra un resquicio entre líneas con una pared rápida y rompe tu bloque.",
        "Chispazo de talento de {riv}: acelera justo entre dos defensores y gana metros.",
        "{rival} mueve el balón rápido de banda a banda, descoloca el bloque y logra avanzar.",
        "Pese al orden defensivo, {riv} se gira en un palmo de terreno y supera la primera línea.",
        "Un pase vertical preciso de {riv} atraviesa tu bloque defensivo.",
    ),
    # ---- Pressing, Marking, Counter, Offside Trap
    "press_win": (
        "¡Presión asfixiante! {def_} encierra a {riv} y roba el balón arriba.",
        "Tu equipo muerde en la salida: {def_} fuerza el error de {riv} y recupera en buena zona.",
        "¡Robo en presión alta! {def_} le madruga el control a {riv}.",
        "Ahogas la salida de {rival} y {def_} se queda con el balón.",
    ),
    "press_fail": (
        "{riv} se quita la presión con un toque de primera y deja mal parada a tu defensa.",
        "La presión llega medio segundo tarde: {rival} sale jugando con espacio a tu espalda.",
        "{rival} rompe tu presión alta con un pase rápido al espacio.",
    ),
    "counter_win": (
        "¡Recuperación y contragolpe fulminante! {def_} corta y lanza a tu equipo con la defensa rival abierta.",
        "¡Trampa perfecta! {def_} intercepta el ataque y sales disparado al contraataque con espacios.",
        "¡A la contra! {def_} roba el balón y encuentra a {rival} completamente desordenado.",
    ),
    "offside_trap_win": (
        "¡Línea adelantada milimétrica! El árbitro levanta la bandera: {riv} cae en fuera de juego.",
        "¡Trampa del fuera de juego perfecta! Tu defensa da el paso adelante a la vez y deja enganchado a {riv}.",
        "¡Fuera de juego! {def_} coordina la línea y anula el ataque de {rival}.",
    ),
    "offside_trap_fail": (
        "¡Falla la trampa del fuera de juego! {riv} arranca habilitado y se escapa solo hacia tu arco.",
        "¡Se durmió un lateral! {riv} rompe el fuera de juego y queda mano a mano.",
    ),
    "mark_win": (
        "Marcaje pegajoso de {def_}: le anticipa el movimiento a {riv} y le limpia el balón.",
        "{def_} no le da respiro a {riv} y le arrebata la pelota con oficio.",
        "Duelo individual ganado por {def_} sobre {riv}. Recuperas la posesión.",
    ),
    "mark_stall": (
        "El marcaje de {def_} incomoda a {riv}, que se gira y no puede progresar.",
        "{def_} le respira en la nuca a {riv} y lo obliga a frenar el ataque.",
        "{riv} no logra sacarse de encima a {def_} y juega hacia un costado sin peligro.",
    ),
    # ---- Attack: Short pass, Switch play, Dribble, Carry, Through ball
    "short_pass_ok": (
        "{pas} toca en corto con {rec} y el equipo progresa. Ahora {zona}.",
        "Combinación limpia entre {pas} y {rec}. Avanzas, ahora {zona}.",
        "{pas} apoya rápido en {rec}, que controla con clase. Estás {zona}.",
        "Pared bien ejecutada entre {pas} y {rec}. Ganas terreno, ahora {zona}.",
    ),
    "short_pass_one_two": (
        "¡Triangulación de lujo! {pas} tira la pared con {rec} y rompen una línea rival. Ahora {zona}.",
        "¡Toque de primera entre {pas} y {rec}! El público aplaude el juego asociado. Estás {zona}.",
    ),
    "short_pass_cut": (
        "{def_} adivina el pase de {pas} hacia {rec} e intercepta.",
        "El envío corto de {pas} queda corto y {def_} corta la jugada.",
        "Pase impreciso de {pas} buscando a {rec}; {rival} recupera.",
    ),
    "switch_play_ok": (
        "¡Gran cambio de frente de {pas} hacia la banda {banda}! {rec} controla con espacio y la defensa rival llega tarde.",
        "{pas} cruza el campo con un envío preciso para {rec} por la banda {banda}. ¡Se abre todo el ataque!",
        "Cambio de orientación perfecto de {pas} a {rec} por la banda {banda}: descolocas al rival.",
    ),
    "switch_play_cut": (
        "El cambio de frente de {pas} se queda corto y {def_} lo corta en el medio.",
        "{pas} busca cambiar de banda, pero el balón sale pasado y {rival} recupera.",
    ),
    "dribble_ok": (
        "{jug} amaga hacia afuera, quiebra a {def_} y avanza. Ahora {zona}.",
        "{jug} encara con decisión a {def_} y lo deja en el camino. Estás {zona}.",
        "Gran recorte de {jug} ante la marca de {def_}. Ahora {zona}.",
    ),
    "dribble_magic": (
        "¡Regate milagroso de {jug}! Deja atrás a {def_} y a un segundo marcador. Ahora {zona}.",
        "¡Caño espectacular de {jug} sobre {def_}! El estadio estalla. Ahora {zona}.",
    ),
    "dribble_skill": (
        "¡Técnica depurada de {jug}! Se saca a {def_} con una pisada limpia. Ahora {zona}.",
        "¡Qué cintura de {jug}! Deja desairado a {def_} y progresa {zona}.",
    ),
    "dribble_lost": (
        "{def_} aguanta bien el amago y le quita el balón a {jug}.",
        "{jug} intenta el regate ante {def_}, pero alarga demasiado el toque y la pierde.",
        "Buen cierre de {def_}, que frena en seco el intento individual de {jug}.",
    ),
    "carry_ok": (
        "{jug} conduce con la cabeza levantada por {banda_txt} y gana metros. Ahora {zona}.",
        "{jug} arranca en conducción protegiendo el balón con el cuerpo. Ahora {zona}.",
        "Zancada potente de {jug} conduciendo el balón. Ahora {zona}.",
    ),
    "carry_lost": (
        "{def_} le cierra el camino a {jug} en plena conducción y le quita el balón.",
        "{jug} se mete en un embudo y {def_} logra puntear el balón.",
    ),
    # ---- Quiet circulation (for 1min / 30sec pace — varied & tactical!)
    "quiet_attack_short": (
        "{pas} mueve el balón con {rec} para abrir huecos, todavía {zona}.",
        "Circulación paciente entre {pas} y {rec}; buscas el espacio {zona}.",
        "{pas} pausa el ritmo y toca de seguridad con {rec}, {zona}.",
    ),
    "quiet_attack_long": (
        "{pas} cambia el ritmo buscando a {rec} sin ganar metros, sigues {zona}.",
        "Mueves la jugada de un costado a otro esperando un desmarque, {zona}.",
    ),
    "quiet_attack_dribble": (
        "{jug} pisa el balón, aguanta la marca de {def_} y protege la posesión {zona}.",
        "{jug} amaga sin arriesgar y conserva el balón {zona}.",
    ),
    "quiet_attack_carry": (
        "{jug} conduce unos metros en diagonal buscando línea de pase, sigues {zona}.",
        "{jug} temporiza con el balón pegado al pie, todavía {zona}.",
    ),
    "quiet_defense_press": (
        "Aprietas la salida y {riv} se ve obligado a soltarla rápido sin avanzar.",
        "Tu presión incomoda a {rival}, que apenas logra conservar el balón en su sitio.",
    ),
    "quiet_defense_mark": (
        "{def_} sigue de cerca a {riv} y le impide girarse hacia el arco.",
        "Buena vigilancia de {def_} sobre {riv}: {rival} no encuentra pase vertical.",
    ),
    "quiet_defense_counter": (
        "Tu equipo aguarda agazapado esperando el error, mientras {rival} mastica la jugada.",
        "Mantienes a tus delanteros listos para correr, pero {rival} cuida el balón sin arriesgar.",
    ),
    # ---- Proactive rival attacking moves (dribbles, through balls, one-twos, long shots)
    "rival_dribble_win": (
        "¡Gambeta eléctrica de {riv}! Deja atrás a {def_} y se mete con peligro hacia el arco.",
        "{riv} encara con descaro a {def_}, lo quiebra hacia adentro y queda de frente al arco.",
        "¡Qué recorte de {riv} sobre {def_}! Rompe la marca y busca el remate.",
    ),
    "rival_dribble_fail": (
        "{riv} intenta la gambeta ante {def_}, pero {def_} aguanta firme y le roba el balón.",
        "{def_} le adivina el amago a {riv} y corta la jugada individual.",
        "Gran cierre de {def_} en el uno contra uno frente a {riv}. ¡Recuperas!",
    ),
    "rival_through_ball_ok": (
        "¡Pase filtrado quirúrgico de {pas} para {rec}! Rompe tu última línea.",
        "{pas} mete un balón al vacío para {rec}, que ataca el espacio con peligro.",
        "¡Alerta! {pas} filtra entre centrales y habilita a {rec} en el área.",
    ),
    "rival_through_ball_cut": (
        "{pas} intenta un pase filtrado buscando a {rec}, pero {def_} lee la jugada e intercepta.",
        "¡Corte providencial de {def_}! Detiene el balón filtrado de {pas} antes de que llegue a {rec}.",
    ),
    "rival_one_two": (
        "Pared rapidísima entre {pas} y {rec}: {rival} acelera y entra a zona de remate.",
        "Combinación de primera de {pas} con {rec} que desacomoda a tu defensa.",
    ),
    "rival_long_shot": (
        "{riv} ve el hueco desde fuera del área y suelta un derechazo potente.",
        "{riv} no lo duda más: se perfila y prueba al arco desde media distancia.",
        "¡Disparo lejano de {riv}, de {rival}! Busca sorprender al portero.",
    ),
    # ---- Dynamic ambient / clash / tension / emotional & tactical shifts
    "midfield_clash": (
        "¡Choque fuerte en la disputa del balón entre {jug} y {riv}! Ambos se levantan rápido y el árbitro deja seguir.",
        "Balón dividido en la mitad de la cancha: {jug} y {riv} van con todo, y el rebote favorece a {ganador}.",
        "¡Duelo chispazo en el medio! {jug} y {riv} meten la pierna sin guardarse nada; sale jugando {ganador}.",
    ),
    "heated_clash": (
        "¡Saltan chispas entre {jug} y {riv}! Se empujan tras la disputa y el árbitro tiene que separarlos.",
        "¡Cruce picante entre {jug} y {riv}! El partido está muy caliente y nadie regala un centímetro. Balón para {ganador}.",
        "¡Fricción máxima en el medio! {jug} y {riv} van al límite del reglamento; la pelota queda para {ganador}.",
    ),
    "tension_rising": (
        "¡El partido se empieza a picar! Hay muchas faltas y los jugadores entran cada vez más fuerte.",
        "Se calienta el encuentro: tras tantos roces, cada pelota dividida se disputa con mucha agresividad.",
    ),
    "tension_boiling": (
        "¡Clima de máxima tensión en la cancha! Los dos equipos están muy agresivos y el árbitro advierte a los capitanes.",
        "¡El partido es una caldera! Hay pierna fuerte en cada rincón y cualquier entrada a destiempo puede terminar en expulsión.",
    ),
    "scuffle_warning": (
        "¡Conato de bronca! {jug} y {riv} se encaran tras la falta, llegan varios compañeros a empujarse y el árbitro pide calma.",
        "¡Se arman los empujones! {jug} le reclama airadamente a {riv} por la entrada; el árbitro interviene para que no pase a mayores.",
    ),
    "rival_tactic_offensive": (
        "El técnico de {rival} pide ir con todo arriba: {rival} adelanta sus líneas y pasa a postura ofensiva.",
        "{rival} no se conforma con el resultado, arriesga más y manda a sus laterales al ataque.",
    ),
    "rival_tactic_defensive": (
        "{rival} decide cuidar el resultado: repliega sus líneas y arma un bloque defensivo más cerrado.",
        "El banco de {rival} ordena cerrar espacios y defender la ventaja con uñas y dientes.",
    ),
    "rival_tactic_balanced": (
        "{rival} reacomoda sus líneas y vuelve a una postura equilibrada en la mitad de la cancha.",
        "El entrenador de {rival} pide orden y equilibrio entre líneas.",
    ),
    "rival_switch_play": (
        "{pas}, de {rival}, cambia de orientación con un pelotazo cruzado hacia la banda {banda} para {rec}.",
        "¡Cambio de frente de {rival}! {pas} abre la cancha hacia la banda {banda} buscando a {rec}.",
    ),
    "unexpected_slip": (
        "¡Increíble! {def_} pierde pie en el momento clave y le deja un hueco enorme a {att}.",
        "¡Mal control en el peor momento! {def_} pifia el despeje y {att} queda con ventaja.",
    ),
    "late_tension": (
        "¡Últimos diez minutos de partido! El ambiente en el estadio se vuelve eléctrico.",
        "Entramos en la recta final: cada balón dividido puede definir el partido.",
    ),
}


class Narrator:
    """Picks varied Spanish narration templates while preventing back-to-back repeats."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._recent: dict[str, deque[int]] = {}

    def say(self, category: str, **kwargs: object) -> str:
        pool = TEMPLATES[category]
        n = len(pool)
        history = self._recent.setdefault(category, deque(maxlen=max(1, min(2, n - 1))))
        candidates = [i for i in range(n) if i not in history] or list(range(n))
        chosen_idx = self._rng.choice(candidates)
        history.append(chosen_idx)
        return pool[chosen_idx].format(**kwargs)
