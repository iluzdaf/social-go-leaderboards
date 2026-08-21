import {h, render} from "preact";
import {useEffect, useMemo, useState} from "preact/hooks";
import Board from "@sabaki/go-board";
import {Goban} from "@sabaki/shudan";
import "@sabaki/shudan/css/goban.css";

const dataElement = document.getElementById("game-review-data");
const mount = document.getElementById("game-review-board");

if (dataElement && mount) {
  const review = JSON.parse(dataElement.textContent || "{}");
  mount.textContent = "";
  render(h(GameReview, {review}), mount);
}

function GameReview({review}) {
  const initialMove = clamp(review.initialMove || 0, 0, review.moves.length);
  const [moveNumber, setMoveNumber] = useState(initialMove);
  const positions = useMemo(() => buildPositions(review), [review]);
  const position = positions[moveNumber] || positions[0];
  const currentMove = review.moves[moveNumber - 1] || null;
  const selectedMoveLabel = moveNumber > 0 ? moveLabel(moveNumber, review.moves.length, currentMove) : "";
  const selectedEvent = review.events.find(
    (event) => Number(event.move_number) === moveNumber
  );

  useEffect(() => {
    function handleMoveEvent(event) {
      setMoveNumber(clamp(Number(event.detail?.moveNumber) || 0, 0, review.moves.length));
    }
    window.addEventListener("game-review-move", handleMoveEvent);
    return () => window.removeEventListener("game-review-move", handleMoveEvent);
  }, [review.moves.length]);

  return h("div", {className: "review-layout"}, [
    h("section", {className: "board-panel", key: "board"}, [
      h("div", {className: "board-shell"}, [
        h(Goban, {
          vertexSize: vertexSizeForBoard(review.boardSize),
          signMap: position.signMap,
          markerMap: markerMapForMove(review.boardSize, currentMove),
          showCoordinates: true,
        }),
      ]),
      h("div", {className: "move-controls"}, [
        h(
          "button",
          {
            type: "button",
            onClick: () => setMoveNumber(Math.max(0, moveNumber - 1)),
            disabled: moveNumber === 0,
          },
          "Previous"
        ),
        h(
          "button",
          {
            type: "button",
            onClick: () => setMoveNumber(Math.min(review.moves.length, moveNumber + 1)),
            disabled: moveNumber === review.moves.length,
          },
          "Next"
        ),
      ]),
      selectedMoveLabel
        ? h("div", {className: "selected-analysis"}, [
            h("strong", null, `${selectedMoveLabel},`),
            selectedEvent
              ? h(
                  "span",
                  null,
                  `${selectedEvent.player}: ${formatNumber(selectedEvent.winrate_before)} to ${formatNumber(selectedEvent.winrate_after)} (${formatNumber(selectedEvent.winrate_drop)} point drop)`
                )
              : null,
          ])
        : null,
    ]),
    h("section", {className: "graph-panel", key: "graph"}, [
      h(WinrateGraph, {events: review.events, moveNumber, setMoveNumber}),
    ]),
  ]);
}

function WinrateGraph({events, moveNumber, setMoveNumber}) {
  if (!events.length) {
    return h("div", {className: "empty-graph"}, "No analysis graph yet.");
  }
  const width = 620;
  const height = 190;
  const padding = 24;
  const maxMove = Math.max(...events.map((event) => Number(event.move_number) || 0), 1);
  const points = events.map((event) => {
    const move = Number(event.move_number) || 0;
    const after = Number(event.winrate_after) || 0;
    return {
      move,
      x: padding + (move / maxMove) * (width - padding * 2),
      y: padding + ((100 - after) / 100) * (height - padding * 2),
      drop: Number(event.winrate_drop) || 0,
    };
  });
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  const selected = points.find((point) => point.move === moveNumber);

  return h("div", {className: "winrate-graph"}, [
    h("div", {className: "graph-title"}, "Win-rate after each move"),
    h("svg", {viewBox: `0 0 ${width} ${height}`, role: "img"}, [
      h("line", {x1: padding, y1: padding, x2: padding, y2: height - padding, className: "graph-axis"}),
      h("line", {x1: padding, y1: height - padding, x2: width - padding, y2: height - padding, className: "graph-axis"}),
      h("path", {d: path, className: "graph-line"}),
      points.map((point) =>
        h("circle", {
          key: point.move,
          className: "graph-hit",
          cx: point.x,
          cy: point.y,
          r: 8,
          title: `Move ${point.move}, ${point.drop.toFixed(1)} point drop`,
          onClick: () => setMoveNumber(point.move),
        })
      ),
      selected
        ? h("circle", {cx: selected.x, cy: selected.y, r: 5, className: "graph-selected"})
        : null,
    ]),
  ]);
}

function buildPositions(review) {
  const positions = [];
  let board = Board.fromDimensions(review.boardSize);
  positions.push({signMap: board.signMap});
  for (const move of review.moves) {
    if (!move.pass) {
      board = board.makeMove(stoneSign(move.color), [move.x, move.y], {
        preventOverwrite: false,
        preventSuicide: false,
        preventKo: false,
      });
    }
    positions.push({signMap: board.signMap});
  }
  return positions;
}

function markerMapForMove(boardSize, move) {
  const markerMap = Array.from({length: boardSize}, () => Array.from({length: boardSize}, () => null));
  if (!move || move.pass) return markerMap;
  markerMap[move.y][move.x] = {type: "circle", label: `Move ${move.moveNumber}`};
  return markerMap;
}

function stoneSign(color) {
  return color === "B" ? 1 : -1;
}

function vertexSizeForBoard(boardSize) {
  if (boardSize <= 9) return 34;
  if (boardSize <= 13) return 27;
  return 22;
}

function moveLabel(moveNumber, total, move) {
  if (moveNumber === 0) return "Start";
  const notation = move?.point || "pass";
  return `Move ${moveNumber}: ${notation}`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : "";
}

window.addEventListener("click", (event) => {
  const row = event.target.closest("[data-move-number]");
  if (!row) return;
  const moveNumber = Number(row.dataset.moveNumber);
  window.dispatchEvent(new CustomEvent("game-review-move", {detail: {moveNumber}}));
});
